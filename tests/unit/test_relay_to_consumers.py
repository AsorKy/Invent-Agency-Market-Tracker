"""Check the path from Core outbox rows to Pulse projections, offline (REQ-EVT-001/002, REQ-QA-004).

Events are created with the real ``create_lead``, ``record_outcome`` and ``create_signal`` code
over the real SQL units of work (against ``support_relay.FakeCoreEngine``), relayed with the real
``relay_due_events`` and ``stream_fields`` into fakeredis, and applied by the real Pulse
``process_event``. It asserts tenant placement, once-only application of a replay, dead lettering
of an event without tenant, and that no fact of one tenant shows up under another.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("sqlalchemy")
pytest.importorskip("structlog")
pytest.importorskip("redis.asyncio")
pytest.importorskip("fakeredis")

from support_relay import FakeCoreEngine, FakePulseDb  # noqa: E402

from libs.contracts.api import SignalCreate  # noqa: E402
from services.core.app.actions import OutcomeCreate, record_outcome  # noqa: E402
from services.core.app.dead_letter import (  # noqa: E402
    MISSING_TENANT_ERROR,
    relay_due_events,
    stream_fields,
)
from services.core.app.leads import LeadCreate, create_lead  # noqa: E402
from services.core.app.repository import CoreRepository  # noqa: E402
from services.pulse.app.consumer import STREAM_NAME, process_event  # noqa: E402

TENANT_A = uuid4()
TENANT_B = uuid4()
OBSERVED = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)


class World:
    """Core engine, repository, and one company per tenant."""

    def __init__(self) -> None:
        self.engine = FakeCoreEngine()
        self.repo = CoreRepository(None)
        self.repo.engine = self.engine  # type: ignore[assignment]
        self.company = {TENANT_A: uuid4(), TENANT_B: uuid4()}
        for tenant, company in self.company.items():
            self.engine.add_company(company, tenant)

    async def lead(self, tenant: UUID) -> UUID:
        body = LeadCreate(company_id=self.company[tenant], service_line="web", city="Bogota")
        response, _ = await create_lead(
            self.repo.lead_unit_of_work(), body, tenant_id=tenant, idempotency_key=str(uuid4())
        )
        return UUID(response["lead_id"])

    async def outcome(self, tenant: UUID, lead_id: UUID) -> None:
        await record_outcome(
            self.repo.action_unit_of_work(),
            lead_id,
            OutcomeCreate(outcome="meeting"),
            tenant_id=tenant,
        )

    async def signal(self, company_id: UUID, key: str) -> UUID:
        signal = SignalCreate(
            company_id=company_id,
            source="rues",
            signal_type="new_company",
            observed_at=OBSERVED,
            source_external_id=key,
        )
        signal_id, _ = await self.repo.create_signal(signal, request_id=None)
        return signal_id


async def relay(world: World, redis: Any) -> dict[str, int]:
    async def publish(row: dict[str, Any]) -> None:
        await redis.xadd(STREAM_NAME, stream_fields(row))

    return await relay_due_events(world.repo.outbox_unit_of_work(), publish)


async def consume(redis: Any, pulse: FakePulseDb) -> None:
    for _message_id, fields in await redis.xrange(STREAM_NAME):
        async with pulse.begin() as connection:
            await process_event(connection, fields)  # type: ignore[arg-type]


async def seeded(redis: Any) -> tuple[World, FakePulseDb, dict[UUID, dict[str, UUID]]]:
    world, pulse = World(), FakePulseDb()
    ids: dict[UUID, dict[str, UUID]] = {}
    for tenant in (TENANT_A, TENANT_B):
        lead_id = await world.lead(tenant)
        await world.outcome(tenant, lead_id)
        ids[tenant] = {
            "lead": lead_id,
            "signal": await world.signal(world.company[tenant], f"sig-{tenant}"),
        }
    counts = await relay(world, redis)
    assert counts == {"published": 6, "retried": 0, "dead_lettered": 0}
    await consume(redis, pulse)
    return world, pulse, ids


async def test_facts_land_under_the_right_tenant(fake_redis: Any) -> None:
    world, pulse, ids = await seeded(fake_redis)
    assert len(pulse.processed) == 6
    for tenant in (TENANT_A, TENANT_B):
        assert {e["tenant_id"] for e in pulse.processed.values()} >= {tenant}
        signal = pulse.signal_facts[ids[tenant]["signal"]]
        assert signal["tenant_id"] == tenant
        assert signal["company_id"] == world.company[tenant]
        (outcome,) = [
            f for f in pulse.outcome_facts.values() if f["lead_id"] == ids[tenant]["lead"]
        ]
        assert outcome["tenant_id"] == tenant
    assert len(pulse.signal_facts) == 2 and len(pulse.outcome_facts) == 2
    for row in world.engine.outbox:
        assert row["published_at"] is not None


async def test_tenant_data_never_crosses_over(fake_redis: Any) -> None:
    world, pulse, ids = await seeded(fake_redis)
    owner = {ids[t]["signal"]: t for t in ids} | {ids[t]["lead"]: t for t in ids}
    for signal_id, fact in pulse.signal_facts.items():
        assert fact["tenant_id"] == owner[signal_id]
        assert fact["company_id"] == world.company[owner[signal_id]]
    for fact in pulse.outcome_facts.values():
        assert fact["tenant_id"] == owner[fact["lead_id"]]
    for tenant in (TENANT_A, TENANT_B):
        rows = [row for key, row in pulse.rollup.items() if key[0] == tenant]
        assert sum(row["signal_count"] for row in rows) == 1
        assert sum(row["lead_count"] for row in rows) == 1
    assert {key[0] for key in pulse.rollup} == {TENANT_A, TENANT_B}
    for entry in pulse.processed.values():
        assert entry["tenant_id"] in (TENANT_A, TENANT_B)


async def test_replayed_entry_is_applied_once(fake_redis: Any) -> None:
    _world, pulse, _ids = await seeded(fake_redis)
    before = (
        dict(pulse.processed),
        dict(pulse.signal_facts),
        dict(pulse.outcome_facts),
        {k: dict(v) for k, v in pulse.rollup.items()},
    )
    entries = await fake_redis.xrange(STREAM_NAME)
    for _message_id, fields in entries:
        await fake_redis.xadd(STREAM_NAME, fields)  # same event_id delivered again
    await consume(fake_redis, pulse)
    assert len(await fake_redis.xrange(STREAM_NAME)) == 12
    after = (pulse.processed, pulse.signal_facts, pulse.outcome_facts, pulse.rollup)
    assert after == before


async def test_event_without_tenant_is_dead_lettered_and_projects_nothing(
    fake_redis: Any,
) -> None:
    world, pulse = World(), FakePulseDb()
    orphan_company = uuid4()  # no company row, so the signal insert resolves no tenant
    await world.signal(orphan_company, "orphan")
    (row,) = world.engine.outbox
    assert row["tenant_id"] is None

    counts = await relay(world, fake_redis)

    assert counts == {"published": 0, "retried": 0, "dead_lettered": 1}
    assert await fake_redis.xrange(STREAM_NAME) == []
    (dead,) = world.engine.dead_letters
    assert dead["error"] == MISSING_TENANT_ERROR and dead["event_id"] == row["event_id"]
    assert row["published_at"] is None and row["dead_lettered_at"] is not None
    await consume(fake_redis, pulse)
    assert pulse.processed == {} and pulse.signal_facts == {} and pulse.rollup == {}


async def test_tenantless_stream_entry_projects_nothing() -> None:
    pulse = FakePulseDb()
    payload = {"signal_id": str(uuid4()), "company_id": str(uuid4())}
    event = {
        "event_id": str(uuid4()),
        "event_type": "signal.created",
        "payload": json.dumps(payload),
    }
    async with pulse.begin() as connection:
        await process_event(connection, event)  # type: ignore[arg-type]
    assert pulse.processed == {} and pulse.signal_facts == {}
