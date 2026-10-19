"""Outbox relay on real Redis Streams and the REQ-QA-004 flow on real stores (T-078).

Skipped unless ``TEST_DATABASE_URL`` and ``TEST_REDIS_URL`` are both set. The real
repositories (``CoreRepository``, ``IngestRepository``, ``MlRepository``) write to a migrated
scratch database, the real ``relay_due_events`` publishes to a unique Redis stream, and the
real Pulse ``process_event`` projects the events, so rows appear in the ``ingest``, ``core``,
``ml`` and ``pulse`` schemas. The gateway, entity resolution and Brief steps are covered by the
offline test ``tests/unit/test_pipeline_flow.py``; they have no SQL of their own to prove here.
The tests connect as the URL user (normally a superuser), so row level security is not
exercised; ``tests/integration/test_sql_postgres.py`` checks that RLS is enabled.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from support_services import asyncpg_url, needs_services

pytestmark = needs_services

TENANT = UUID("11111111-1111-4111-8111-111111111111")
GROUP = "imt-test-pulse"


@pytest.fixture
async def core(database_url: str) -> AsyncIterator[Any]:
    pytest.importorskip("asyncpg")
    from services.core.app.repository import CoreRepository

    repository = CoreRepository(asyncpg_url(database_url))
    try:
        yield repository
    finally:
        await repository.close()


async def _scalar(repository: Any, sql: str, **params: Any) -> Any:
    from sqlalchemy import text

    async with repository.engine.connect() as connection:
        return (await connection.execute(text(sql), params)).scalar_one()


async def _create_company(core: Any, name: str = "Acme Integration SAS") -> UUID:
    from services.core.app.companies import CompanyUpsert, upsert_company

    result = await upsert_company(
        core.company_unit_of_work(),
        CompanyUpsert(legal_name=name, city="Medellin", tenant_id=TENANT),
    )
    assert result["status"] == "created"
    return UUID(str(result["company_id"]))


def _publisher(redis: Any, stream: str) -> Any:
    from services.core.app.dead_letter import stream_fields

    async def publish(row: dict[str, Any]) -> None:
        await redis.xadd(stream, stream_fields(row))

    return publish


async def test_relay_publishes_each_outbox_event_once_to_a_real_stream(
    core: Any, redis_stream: tuple[Any, str]
) -> None:
    from services.core.app.dead_letter import relay_due_events

    redis, stream = redis_stream
    company_id = await _create_company(core, "Relay Once SAS")
    unpublished = "SELECT count(*) FROM core.outbox_event WHERE published_at IS NULL"
    before = await _scalar(core, unpublished)
    assert before >= 1

    first = await relay_due_events(core.outbox_unit_of_work(), _publisher(redis, stream))
    second = await relay_due_events(core.outbox_unit_of_work(), _publisher(redis, stream))

    assert first["published"] == before and first["dead_lettered"] == 0
    assert second == {"published": 0, "retried": 0, "dead_lettered": 0}
    entries = await redis.xrange(stream)
    assert len(entries) == before
    mine = [fields for _id, fields in entries if fields["aggregate_id"] == str(company_id)]
    assert len(mine) == 1
    assert mine[0]["event_type"] == "company.updated"
    assert mine[0]["tenant_id"] == str(TENANT)


async def test_relay_retries_a_failed_publish_and_then_delivers(
    core: Any, redis_stream: tuple[Any, str]
) -> None:
    from services.core.app.dead_letter import relay_due_events

    redis, stream = redis_stream
    await _create_company(core, "Relay Retry SAS")
    # Drain everything earlier tests left behind, then create one fresh event.
    await relay_due_events(core.outbox_unit_of_work(), _publisher(redis, stream))
    await redis.delete(stream)
    company_id = await _create_company(core, "Relay Retry Two SAS")

    async def broken(row: dict[str, Any]) -> None:
        raise ConnectionError("redis down")

    now = datetime.now(UTC)
    failed = await relay_due_events(core.outbox_unit_of_work(), broken, now=now)
    assert failed == {"published": 0, "retried": 1, "dead_lettered": 0}
    not_due = await relay_due_events(
        core.outbox_unit_of_work(), _publisher(redis, stream), now=now + timedelta(seconds=1)
    )
    assert not_due["published"] == 0
    due = await relay_due_events(
        core.outbox_unit_of_work(), _publisher(redis, stream), now=now + timedelta(minutes=1)
    )
    assert due["published"] == 1
    entries = await redis.xrange(stream)
    assert [fields["aggregate_id"] for _id, fields in entries] == [str(company_id)]
    attempts = await _scalar(
        core,
        "SELECT attempts FROM core.outbox_event WHERE aggregate_id = :id",
        id=company_id,
    )
    assert attempts == 1


async def test_relay_dead_letters_an_event_without_a_tenant(
    core: Any, redis_stream: tuple[Any, str]
) -> None:
    from sqlalchemy import text

    from services.core.app.dead_letter import relay_due_events

    redis, stream = redis_stream
    await relay_due_events(core.outbox_unit_of_work(), _publisher(redis, stream))
    event_id = uuid4()
    async with core.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO core.outbox_event (event_id, aggregate_type, aggregate_id, "
                "event_type, version, payload) "
                "VALUES (:e, 'company', :a, 'company.updated', 1, CAST('{}' AS jsonb))"
            ),
            {"e": event_id, "a": uuid4()},
        )
    counts = await relay_due_events(core.outbox_unit_of_work(), _publisher(redis, stream))
    assert counts == {"published": 0, "retried": 0, "dead_lettered": 1}
    assert await redis.xlen(stream) == 0
    parked = await _scalar(
        core,
        "SELECT count(*) FROM core.outbox_dead_letter WHERE event_id = :e",
        e=event_id,
    )
    assert parked == 1


async def test_flow_leaves_rows_in_every_expected_schema(
    database_url: str, core: Any, redis_stream: tuple[Any, str]
) -> None:
    from support_services import BASE_REDIS_URL

    from libs.common.db import create_engine
    from libs.common.redis_streams import ensure_consumer_group, read_consumer_batch
    from libs.contracts.api import SignalCreate
    from services.core.app.dead_letter import relay_due_events
    from services.ingest.app.repository import IngestRepository
    from services.ml.app.repository import MlRepository
    from services.pulse.app.consumer import process_event

    redis, stream = redis_stream
    assert BASE_REDIS_URL
    url = asyncpg_url(database_url)

    # ingest: the snapshot a webhook would have stored
    ingest = IngestRepository(url, BASE_REDIS_URL)
    try:
        snapshot_id = f"snap-{uuid4().hex}"
        row_id, created, _queued = await ingest.persist_snapshot(
            snapshot_id=snapshot_id,
            dataset_id="gd_integration",
            payload=[{"name": "Acme"}],
            payload_sha256="a" * 64,
            fetched_at=datetime.now(UTC),
        )
        assert created
    finally:
        await ingest.close()

    # core: company, signal, and the outbox events that describe them
    company_id = await _create_company(core, "Flow SAS")
    observed = datetime.now(UTC)
    signal_id, event_id = await core.create_signal(
        SignalCreate(
            company_id=company_id,
            source="maps",
            signal_type="new_branch",
            observed_at=observed,
            strength=0.8,
            payload={"city": "Medellin", "service_line": "retail"},
        ),
        request_id="req-integration",
    )
    assert event_id is not None

    # ml: the score the scoring service would store
    ml = MlRepository(url)
    try:
        await ml.upsert_score(
            company_id=company_id,
            score=82,
            is_hot=True,
            source_version="2",
            score_version="1",
        )
    finally:
        await ml.close()

    # relay to a real stream, then the Pulse projection from that stream
    counts = await relay_due_events(core.outbox_unit_of_work(), _publisher(redis, stream))
    assert counts["published"] >= 2 and counts["dead_lettered"] == 0
    await ensure_consumer_group(redis, stream, GROUP)
    batch = await read_consumer_batch(
        redis, stream=stream, group=GROUP, consumer="c1", count=100, block=1000
    )
    events = [fields for _stream, entries in batch for _id, fields in entries]
    types = {fields["event_type"] for fields in events}
    assert {"company.updated", "signal.created"} <= types
    pulse_engine = create_engine(url, pool_size=1)
    assert pulse_engine is not None
    try:
        for entry_id, fields in _entries(batch):
            async with pulse_engine.begin() as connection:
                await process_event(connection, fields)
            await redis.xack(stream, GROUP, entry_id)
    finally:
        await pulse_engine.dispose()

    assert (
        await _scalar(core, "SELECT count(*) FROM ingest.raw_snapshot WHERE id = :i", i=row_id) == 1
    )
    assert await _scalar(core, "SELECT count(*) FROM core.company WHERE id = :i", i=company_id) == 1
    assert await _scalar(core, "SELECT count(*) FROM core.signal WHERE id = :i", i=signal_id) == 1
    assert (
        await _scalar(
            core, "SELECT count(*) FROM ml.lead_score WHERE company_id = :i", i=company_id
        )
        == 1
    )
    assert (
        await _scalar(
            core, "SELECT count(*) FROM pulse.fact_signal WHERE signal_id = :i", i=signal_id
        )
        == 1
    )
    rollup = await _scalar(
        core,
        "SELECT COALESCE(sum(signal_count), 0) FROM pulse.daily_rollup WHERE tenant_id = :t",
        t=TENANT,
    )
    assert rollup >= 1
    pending = await redis.xpending(stream, GROUP)
    assert pending["pending"] == 0

    # a repeated delivery changes nothing (the processed_event ledger)
    pulse_engine = create_engine(url, pool_size=1)
    assert pulse_engine is not None
    try:
        signal_event = next(f for f in events if f["event_type"] == "signal.created")
        async with pulse_engine.begin() as connection:
            await process_event(connection, signal_event)
    finally:
        await pulse_engine.dispose()
    assert (
        await _scalar(
            core, "SELECT count(*) FROM pulse.fact_signal WHERE signal_id = :i", i=signal_id
        )
        == 1
    )


def _entries(batch: Any) -> list[tuple[str, dict[str, str]]]:
    return [(entry_id, fields) for _stream, entries in batch for entry_id, fields in entries]
