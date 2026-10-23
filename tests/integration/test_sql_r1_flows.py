"""Run the SQL added since wave r1-g on a real PostgreSQL (T-162, REQ-QA-001, REQ-SEC-005).

``test_sql_postgres.py`` only prepares statements. Here each flow RUNS on a migrated scratch
database, as the app role that owns the code in production (``core_app``, ``ml_app``,
``pulse_app`` from ``ops/bootstrap_roles.py``), so row level security applies. Flows: KILL
decision (status, version, audit row, outbox, concurrency), ``lead.score_updated`` storage,
ML score upsert with features, chain events in the outbox, Pulse project snapshot upsert, and
tenant isolation of tenant tables with and without ``set_tenant_context``. Fixtures are written
by the server user; asserts on app-role writes read back through the server user. Skipped unless
``TEST_DATABASE_URL`` is set (a superuser server URL); needs the ``sqltest`` extra.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from support_services import asyncpg_url, migrated_database, needs_database

pytestmark = needs_database

PASSWORD = "imt-r1-flows-only"
TENANT = UUID("aaaaaaaa-0000-4000-8000-000000000001")
OTHER = UUID("bbbbbbbb-0000-4000-8000-000000000002")


class Db:
    """URLs of one migrated scratch database: the server user and each app role."""

    def __init__(self, admin: str) -> None:
        from support_sql_pg import bootstrap_app_roles, role_url

        roles = bootstrap_app_roles(admin, PASSWORD)
        self.admin = admin
        self.app = {schema: role_url(admin, role, PASSWORD) for schema, role in roles.items()}

    def rows(self, sql_text: str, *params: Any) -> list[tuple[Any, ...]]:
        from support_sql_pg import admin_execute

        return admin_execute(self.admin, sql_text, params)

    def one(self, sql_text: str, *params: Any) -> tuple[Any, ...]:
        found = self.rows(sql_text, *params)
        assert len(found) == 1, found
        return found[0]


@pytest.fixture(scope="module")
def db() -> Iterator[Db]:
    with migrated_database() as url:
        yield Db(url)


@pytest.fixture
async def engines(db: Db) -> AsyncIterator[dict[str, Any]]:
    """One asyncpg engine per app role, disposed after the test."""
    pytest.importorskip("asyncpg")
    from libs.common.db import create_engine

    made = {schema: create_engine(asyncpg_url(url), pool_size=4) for schema, url in db.app.items()}
    try:
        yield made
    finally:
        for engine in made.values():
            if engine is not None:
                await engine.dispose()


def _company(db: Db, tenant: UUID | None) -> UUID:
    company_id = uuid4()
    db.rows(
        "INSERT INTO core.company (id, tenant_id, slug, legal_name, city) "
        "VALUES (%s, %s, %s, %s, 'Medellin')",
        company_id,
        tenant,
        f"c-{company_id.hex[:12]}",
        f"Company {company_id.hex[:6]}",
    )
    return company_id


def _project(
    db: Db, tenant: UUID, status: str = "draft", metadata: dict[str, Any] | None = None
) -> UUID:
    project_id = uuid4()
    db.rows(
        "INSERT INTO core.project (id, tenant_id, name, service_line, hypothesis, status, metadata)"
        " VALUES (%s, %s, 'Pilot', 'consulting', 'h', %s, %s::jsonb)",
        project_id,
        tenant,
        status,
        json.dumps(metadata or {}),
    )
    return project_id


def _checklist() -> Any:
    from libs.domain.funnel import Checklist, ChecklistItem

    return Checklist(
        version=3,
        example=True,
        gate="G3",
        name="r1 flows",
        items=[ChecklistItem(id="k", op="eq", threshold=0, type="kill")],
    )


# --- T-146: funnel decisions ------------------------------------------------------------


async def test_kill_decision_sets_status_bumps_version_and_writes_audit_and_outbox(
    db: Db, engines: dict[str, Any]
) -> None:
    from services.core.app.funnel import KILLED_STATUS, evaluate_project_gate, funnel_unit_of_work

    project = _project(db, TENANT, metadata={"keep": "me"})
    decision = await evaluate_project_gate(
        funnel_unit_of_work(engines["core"], TENANT),
        project,
        "G3",
        {"k": 1},
        tenant_id=TENANT,
        checklist=_checklist(),
        apply_kill=True,
        expected_version=1,
    )
    assert decision.verdict.value == "KILL"
    status, version, keep = db.one(
        "SELECT status, metadata->>'version', metadata->>'keep' FROM core.project WHERE id = %s",
        project,
    )
    assert (status, version, keep) == (KILLED_STATUS, "2", "me")
    action, tenant, verdict = db.one(
        "SELECT action, tenant_id, payload->>'verdict' FROM core.audit_log WHERE aggregate_id = %s",
        project,
    )
    assert (action, tenant, verdict) == ("funnel.gate_decision", TENANT, "KILL")
    types = sorted(
        r[0]
        for r in db.rows(
            "SELECT event_type FROM core.outbox_event WHERE aggregate_id = %s", project
        )
    )
    assert types == ["funnel.gate_decision", "funnel.kill_trigger_event", "funnel.project_killed"]


async def test_non_kill_decision_keeps_status_and_version(db: Db, engines: dict[str, Any]) -> None:
    from services.core.app.funnel import evaluate_project_gate, funnel_unit_of_work

    project = _project(db, TENANT)
    decision = await evaluate_project_gate(
        funnel_unit_of_work(engines["core"], TENANT),
        project,
        "G3",
        {"k": 0},
        tenant_id=TENANT,
        checklist=_checklist(),
        apply_kill=True,
    )
    assert decision.verdict.value == "ADVANCE"
    assert db.one(
        "SELECT status, metadata->>'version' FROM core.project WHERE id = %s", project
    ) == ("draft", None)
    assert db.one("SELECT count(*) FROM core.audit_log WHERE aggregate_id = %s", project) == (1,)


async def test_killed_project_refuses_a_second_decision_and_writes_nothing(
    db: Db, engines: dict[str, Any]
) -> None:
    from services.core.app.funnel import (
        ProjectKilledError,
        evaluate_project_gate,
        funnel_unit_of_work,
    )

    project = _project(db, TENANT, status="killed", metadata={"version": 2})
    with pytest.raises(ProjectKilledError):
        await evaluate_project_gate(
            funnel_unit_of_work(engines["core"], TENANT),
            project,
            "G3",
            {"k": 0},
            tenant_id=TENANT,
            checklist=_checklist(),
        )
    assert db.one("SELECT count(*) FROM core.audit_log WHERE aggregate_id = %s", project) == (0,)
    assert db.one("SELECT count(*) FROM core.outbox_event WHERE aggregate_id = %s", project) == (0,)


async def test_project_of_another_tenant_is_not_found_for_the_app_role(
    db: Db, engines: dict[str, Any]
) -> None:
    from services.core.app.funnel import (
        ProjectNotFoundError,
        evaluate_project_gate,
        funnel_unit_of_work,
    )

    project = _project(db, OTHER)
    with pytest.raises(ProjectNotFoundError):
        await evaluate_project_gate(
            funnel_unit_of_work(engines["core"], TENANT),
            project,
            "G3",
            {"k": 1},
            tenant_id=TENANT,
            checklist=_checklist(),
            apply_kill=True,
        )
    assert db.one("SELECT status FROM core.project WHERE id = %s", project) == ("draft",)


async def test_two_concurrent_kill_decisions_kill_the_project_once(
    db: Db, engines: dict[str, Any]
) -> None:
    from services.core.app.funnel import (
        ProjectKilledError,
        StaleProjectVersionError,
        evaluate_project_gate,
        funnel_unit_of_work,
    )

    project = _project(db, TENANT)

    async def decide() -> object:
        return await evaluate_project_gate(
            funnel_unit_of_work(engines["core"], TENANT),
            project,
            "G3",
            {"k": 1},
            tenant_id=TENANT,
            checklist=_checklist(),
            apply_kill=True,
        )

    results = await asyncio.wait_for(
        asyncio.gather(decide(), decide(), return_exceptions=True), timeout=30
    )
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(failures) == 1
    assert isinstance(failures[0], ProjectKilledError | StaleProjectVersionError)
    assert db.one(
        "SELECT status, metadata->>'version' FROM core.project WHERE id = %s", project
    ) == ("killed", "2")
    assert db.one("SELECT count(*) FROM core.audit_log WHERE aggregate_id = %s", project) == (1,)


# --- T-145: lead.score_updated ----------------------------------------------------------


_SCORE_ROW = "SELECT score, score_source_version, score_updated_at FROM core.company WHERE id = %s"
MOMENT = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


async def _store(repository: Any, company: UUID, score: int, version: str | None) -> bool:
    stored: bool = await repository.store_company_score(
        company_id=company, tenant_id=TENANT, score=score, source_version=version, now=MOMENT
    )
    return stored


async def test_score_event_stores_the_company_score_and_a_second_delivery_changes_nothing(
    db: Db,
) -> None:
    company = _company(db, TENANT)
    repository = _core_repository(db)
    try:
        assert await _store(repository, company, 72, "5") is True
        first = db.one(_SCORE_ROW, company)
        assert await _store(repository, company, 72, "5") is True  # equal version: same values
        second = db.one(_SCORE_ROW, company)
    finally:
        await repository.close()
    assert first == (72, "5", MOMENT)
    assert second == first


async def test_score_storage_never_lowers_a_newer_version_and_takes_a_newer_one(db: Db) -> None:
    company = _company(db, TENANT)
    repository = _core_repository(db)
    try:
        await _store(repository, company, 60, "10")
        assert await _store(repository, company, 10, "9") is False
        assert db.one(_SCORE_ROW, company)[:2] == (60, "10")
        assert await _store(repository, company, 80, "11") is True
        assert db.one(_SCORE_ROW, company)[:2] == (80, "11")
    finally:
        await repository.close()


async def test_score_of_another_tenant_company_is_not_written(db: Db) -> None:
    company = _company(db, OTHER)
    repository = _core_repository(db)
    try:
        assert await _store(repository, company, 50, "1") is False
    finally:
        await repository.close()
    assert db.one(_SCORE_ROW, company) == (None, None, None)


def _core_repository(db: Db) -> Any:
    from services.core.app.repository import CoreRepository

    return CoreRepository(asyncpg_url(db.app["core"]))


# --- T-151: ML score upsert -------------------------------------------------------------


async def test_ml_score_upsert_stores_features_weights_and_history(db: Db) -> None:
    from services.ml.app.repository import MlRepository

    company = uuid4()
    repository = MlRepository(asyncpg_url(db.app["ml"]))
    columns = (
        "score, is_hot, stale, score_version, feature_icp_fit, feature_signal_strength, "
        "feature_territory_opportunity, weight_icp_fit, weight_signal_strength, weight_territory"
    )
    try:
        await repository.upsert_score(
            company_id=company,
            score=64,
            is_hot=False,
            source_version="3",
            score_version="v1",
            features=(0.123456789, 0.5, 0.25),
            weights=(40.0, 35.0, 25.0),
        )
        row = db.one(f"SELECT {columns} FROM ml.lead_score WHERE company_id = %s", company)
        assert row[:4] == (64, False, False, "v1")
        assert row[4:7] == (Decimal("0.123456789"), Decimal("0.500000000"), Decimal("0.250000000"))
        assert row[7:] == (Decimal("40.000000"), Decimal("35.000000"), Decimal("25.000000"))
        await repository.upsert_score(
            company_id=company,
            score=90,
            is_hot=True,
            source_version="4",
            score_version="v2",
            features=(1.0, 1.0, 1.0),
            weights=(50.0, 30.0, 20.0),
        )
        again = db.one(f"SELECT {columns} FROM ml.lead_score WHERE company_id = %s", company)
        assert again[:4] == (90, True, False, "v2") and again[7] == Decimal("50.000000")
        assert db.one("SELECT count(*) FROM ml.lead_score WHERE company_id = %s", company) == (1,)
        assert db.one("SELECT count(*) FROM ml.score_history WHERE company_id = %s", company) == (
            2,
        )
        inputs = await repository.score_inputs(company)
    finally:
        await repository.close()
    assert inputs is not None and inputs["feature_icp_fit"] == Decimal("1.000000000")


async def test_ml_score_without_features_stores_nulls_and_has_no_inputs(db: Db) -> None:
    from services.ml.app.repository import MlRepository

    company = uuid4()
    repository = MlRepository(asyncpg_url(db.app["ml"]))
    try:
        await repository.upsert_score(
            company_id=company, score=10, is_hot=False, source_version="1", score_version="v1"
        )
        assert db.one(
            "SELECT feature_icp_fit, weight_territory FROM ml.lead_score WHERE company_id = %s",
            company,
        ) == (None, None)
        assert await repository.score_inputs(company) is None
    finally:
        await repository.close()


# --- T-159: chain events in the outbox ---------------------------------------------------


def _chain(parent_version: str = "core-chain-v1") -> list[dict[str, Any]]:
    from libs.domain.event_chain import chain_events_after

    parent = {"event_id": str(uuid4()), "event_type": "signal.created", "source_version": "p1"}
    steps: list[tuple[str, dict[str, Any], str]] = [
        ("signal.tagged", {"signal_id": str(uuid4())}, parent_version),
        ("signal.scored", {"score": 1}, "ml-v1"),
        ("company.matched", {"company": "x"}, "core-chain-v1"),
    ]
    events = chain_events_after(parent, steps, enabled=True)
    assert len(events) == 3
    return events


async def test_insert_chain_events_writes_outbox_rows_with_parent_links(
    db: Db, engines: dict[str, Any]
) -> None:
    from libs.common.tenant import set_tenant_context
    from services.core.app.repository import insert_chain_events

    events = _chain()
    aggregate = uuid4()
    async with engines["core"].begin() as connection:
        await set_tenant_context(connection, TENANT)
        await insert_chain_events(
            connection, events, aggregate_type="signal", aggregate_id=aggregate, tenant_id=TENANT
        )
    rows = db.rows(
        "SELECT event_id, event_type, tenant_id, version, published_at, payload "
        "FROM core.outbox_event WHERE aggregate_id = %s ORDER BY id",
        aggregate,
    )
    assert [r[1] for r in rows] == [e["event_type"] for e in events]
    assert all(r[2] == TENANT and r[3] == 1 and r[4] is None for r in rows)
    assert [str(r[0]) for r in rows] == [e["event_id"] for e in events]
    parents = [r[5]["parent"]["event_id"] for r in rows]
    assert parents[1:] == [e["event_id"] for e in events[:-1]]
    assert rows[0][5]["parent"]["event_type"] == "signal.created"
    assert rows[2][5]["source_version"].count("<") == 3


async def test_insert_chain_events_takes_the_tenant_from_the_company_row(
    db: Db, engines: dict[str, Any]
) -> None:
    from libs.common.tenant import set_tenant_context
    from services.core.app.repository import insert_chain_events

    company = _company(db, TENANT)
    events = _chain()
    aggregate = uuid4()
    async with engines["core"].begin() as connection:
        await set_tenant_context(connection, TENANT)
        await insert_chain_events(
            connection, events, aggregate_type="lead", aggregate_id=aggregate, company_id=company
        )
    tenants = {
        r[0]
        for r in db.rows(
            "SELECT tenant_id FROM core.outbox_event WHERE aggregate_id = %s", aggregate
        )
    }
    assert tenants == {TENANT}


async def test_chain_events_roll_back_with_their_transaction(
    db: Db, engines: dict[str, Any]
) -> None:
    from services.core.app.repository import insert_chain_events

    aggregate = uuid4()
    with pytest.raises(RuntimeError):
        async with engines["core"].begin() as connection:
            await insert_chain_events(
                connection,
                _chain(),
                aggregate_type="signal",
                aggregate_id=aggregate,
                tenant_id=TENANT,
            )
            raise RuntimeError("fail after the outbox write")
    assert db.one("SELECT count(*) FROM core.outbox_event WHERE aggregate_id = %s", aggregate) == (
        0,
    )


# --- T-147: Pulse project snapshot --------------------------------------------------------


async def test_pulse_project_snapshot_upsert_is_idempotent(db: Db, engines: dict[str, Any]) -> None:
    from libs.common.tenant import set_tenant_context
    from services.pulse.app.project_projection import apply_project_created

    project = uuid4()
    payload: dict[str, object] = {"project_id": str(project), "service_line": "consulting"}
    for _ in range(2):
        async with engines["pulse"].begin() as connection:
            await set_tenant_context(connection, TENANT)
            await apply_project_created(connection, TENANT, payload)
    assert db.one(
        "SELECT stage, service_line FROM pulse.project_snapshot WHERE project_id = %s", project
    ) == ("draft", "consulting")
    assert db.one(
        "SELECT count(*) FROM pulse.project_stage_visit WHERE project_id = %s", project
    ) == (1,)


async def test_pulse_consumer_replays_a_project_event_once(db: Db, engines: dict[str, Any]) -> None:
    from services.pulse.app.consumer import process_event

    project = uuid4()
    event = {
        "event_id": str(uuid4()),
        "event_type": "signal.converted_to_project",
        "tenant_id": str(TENANT),
        "payload": json.dumps({"project_id": str(project), "service_line": "ops"}),
    }
    for _ in range(2):
        async with engines["pulse"].begin() as connection:
            await process_event(connection, event)
    assert db.one("SELECT count(*) FROM pulse.project_snapshot WHERE project_id = %s", project) == (
        1,
    )
    assert db.one(
        "SELECT count(*) FROM pulse.processed_event WHERE event_id = %s", UUID(event["event_id"])
    ) == (1,)


# --- REQ-SEC-005: tenant context ------------------------------------------------------------


async def test_tenant_table_read_needs_the_tenant_context(db: Db, engines: dict[str, Any]) -> None:
    from sqlalchemy import text

    from libs.common.tenant import set_tenant_context

    mine = _company(db, TENANT)
    theirs = _company(db, OTHER)
    query = text("SELECT id FROM core.company WHERE id IN (:a, :b)")
    params = {"a": mine, "b": theirs}
    async with engines["core"].connect() as connection:
        assert (await connection.execute(query, params)).all() == []
    async with engines["core"].connect() as connection:
        await set_tenant_context(connection, TENANT)
        assert [r[0] for r in (await connection.execute(query, params)).all()] == [mine]
    async with engines["core"].connect() as connection:
        await set_tenant_context(connection, OTHER)
        assert [r[0] for r in (await connection.execute(query, params)).all()] == [theirs]


async def test_forced_rls_pulse_table_hides_rows_without_context(
    db: Db, engines: dict[str, Any]
) -> None:
    from sqlalchemy import text

    from libs.common.tenant import set_tenant_context

    project = uuid4()
    async with engines["pulse"].begin() as connection:
        await set_tenant_context(connection, TENANT)
        await connection.execute(
            text(
                "INSERT INTO pulse.project_snapshot (tenant_id, project_id, stage, service_line)"
                " VALUES (:t, :p, 'draft', 'x')"
            ),
            {"t": TENANT, "p": project},
        )
    query = text("SELECT count(*) FROM pulse.project_snapshot WHERE project_id = :p")
    async with engines["pulse"].connect() as connection:
        assert (await connection.execute(query, {"p": project})).scalar_one() == 0
    async with engines["pulse"].connect() as connection:
        await set_tenant_context(connection, OTHER)
        assert (await connection.execute(query, {"p": project})).scalar_one() == 0
    async with engines["pulse"].connect() as connection:
        await set_tenant_context(connection, TENANT)
        assert (await connection.execute(query, {"p": project})).scalar_one() == 1


# --- T-168: qualification applies the tenant of the score event --------------------------


def _score_fields(company: UUID, score: int, tenant: UUID, event_id: UUID | None = None) -> Any:
    return {
        "event_id": str(event_id or uuid4()),
        "event_type": "lead.score_updated",
        "aggregate_id": str(company),
        "score": str(score),
        "tenant_id": str(tenant),
    }


_LEAD_ROWS = "SELECT tenant_id, company_id, stage, version FROM core.lead WHERE company_id = %s"


async def test_score_event_creates_then_advances_the_lead_of_the_event_tenant(db: Db) -> None:
    from services.core.app.worker import build_handlers

    company = _company(db, TENANT)
    repository = _core_repository(db)
    handler = build_handlers(repository.qualification_unit_of_work)["lead.score_updated"]
    try:
        first = _score_fields(company, 10, TENANT)
        await handler(first)
        assert db.one(_LEAD_ROWS, company) == (TENANT, company, "discovered", 1)
        await handler(_score_fields(company, 95, TENANT))
        advanced = db.one(_LEAD_ROWS, company)
        await handler(first)  # second delivery of the first event
        assert db.one(_LEAD_ROWS, company) == advanced
    finally:
        await repository.close()
    assert advanced == (TENANT, company, "hot", 3)


async def test_score_event_of_another_tenant_creates_no_lead(db: Db) -> None:
    from services.core.app.worker import build_handlers

    company = _company(db, TENANT)
    repository = _core_repository(db)
    handler = build_handlers(repository.qualification_unit_of_work)["lead.score_updated"]
    try:
        await handler(_score_fields(company, 95, OTHER))
    finally:
        await repository.close()
    assert db.rows(_LEAD_ROWS, company) == []


# --- T-176: ml outbox ----------------------------------------------------------------------


def _scored_events(company: UUID) -> list[dict[str, Any]]:
    from services.ml.app.scoring_service import chain_outbox_events

    return chain_outbox_events(
        company_id=company, tenant_id=TENANT, score=70, source_version="2", parent_event_id=uuid4()
    )


async def test_ml_score_and_outbox_row_commit_together(db: Db) -> None:
    from services.ml.app.repository import MlRepository

    company = uuid4()
    repository = MlRepository(asyncpg_url(db.app["ml"]))
    try:
        await repository.upsert_score(
            company_id=company,
            score=70,
            is_hot=False,
            source_version="2",
            score_version="v1",
            outbox_events=_scored_events(company),
        )
    finally:
        await repository.close()
    assert db.one("SELECT count(*) FROM ml.lead_score WHERE company_id = %s", company) == (1,)
    assert db.one(
        "SELECT event_type, tenant_id FROM ml.outbox_event WHERE aggregate_id = %s", company
    ) == ("signal.scored", TENANT)


async def test_ml_score_and_outbox_row_roll_back_together(db: Db) -> None:
    from services.ml.app.repository import MlRepository

    company = uuid4()
    events = _scored_events(company)
    # A duplicate event_id violates the unique key on the second insert, after the score write.
    events.append(dict(events[0]))
    repository = MlRepository(asyncpg_url(db.app["ml"]))
    try:
        with pytest.raises(Exception):  # noqa: B017 - the driver wraps the unique violation
            await repository.upsert_score(
                company_id=company,
                score=70,
                is_hot=False,
                source_version="2",
                score_version="v1",
                outbox_events=events,
            )
    finally:
        await repository.close()
    assert db.one("SELECT count(*) FROM ml.lead_score WHERE company_id = %s", company) == (0,)
    assert db.one("SELECT count(*) FROM ml.score_history WHERE company_id = %s", company) == (0,)
    assert db.one("SELECT count(*) FROM ml.outbox_event WHERE aggregate_id = %s", company) == (0,)


async def test_ml_relay_publishes_the_row_with_its_tenant(db: Db) -> None:
    from libs.messaging.relay import relay_due_events, stream_fields
    from services.ml.app.repository import MlRepository

    company = uuid4()
    repository = MlRepository(asyncpg_url(db.app["ml"]))
    published: list[dict[str, str]] = []

    async def publish(row: dict[str, Any]) -> None:
        if row["aggregate_id"] == company:
            published.append(stream_fields(row))

    try:
        await repository.upsert_score(
            company_id=company,
            score=70,
            is_hot=False,
            source_version="2",
            score_version="v1",
            outbox_events=_scored_events(company),
        )
        counts = await relay_due_events(repository.outbox_unit_of_work(), publish)
    finally:
        await repository.close()
    assert counts["published"] >= 1 and counts["dead_lettered"] == 0
    assert len(published) == 1
    assert published[0]["tenant_id"] == str(TENANT)
    assert published[0]["event_type"] == "signal.scored"
    assert db.one(
        "SELECT published_at IS NOT NULL FROM ml.outbox_event WHERE aggregate_id = %s", company
    ) == (True,)
    assert db.one("SELECT count(*) FROM ml.outbox_dead_letter WHERE tenant_id = %s", TENANT) == (0,)


# --- T-082: row level security on core.signal, core.location, core.lead_outcome ---------------
# Writers and readers found (all run as core_app): ``CoreRepository.create_signal`` (signal
# insert), ``record_outcome`` through ``action_unit_of_work`` (outcome insert), the retention
# deletes of ``SqlRetentionTransaction.delete_company_data`` (all three tables; the only code
# that touches core.location), the readers ``read_brief_facts`` and ``read_score_features``
# (core.signal). Each case fails when the tenant context is missing.


def _lead(db: Db, tenant: UUID, company: UUID) -> UUID:
    lead_id = uuid4()
    db.rows(
        "INSERT INTO core.lead (id, tenant_id, company_id, service_line) VALUES (%s, %s, %s, 'x')",
        lead_id,
        tenant,
        company,
    )
    return lead_id


def _fixture_signal(db: Db, tenant: UUID, company: UUID, signal_type: str = "hiring") -> UUID:
    signal_id = uuid4()
    db.rows(
        "INSERT INTO core.signal (id, tenant_id, company_id, source, source_external_id,"
        " signal_type, observed_at) VALUES (%s, %s, %s, 'maps', %s, %s, now())",
        signal_id,
        tenant,
        company,
        f"x-{signal_id.hex}",
        signal_type,
    )
    return signal_id


def _fixture_location(db: Db, tenant: UUID, company: UUID) -> UUID:
    location_id = uuid4()
    db.rows(
        "INSERT INTO core.location (id, tenant_id, company_id, city) VALUES (%s, %s, %s, 'Cali')",
        location_id,
        tenant,
        company,
    )
    return location_id


def _fixture_outcome(db: Db, tenant: UUID, lead: UUID) -> UUID:
    outcome_id = uuid4()
    db.rows(
        "INSERT INTO core.lead_outcome (id, tenant_id, lead_id, outcome, occurred_at)"
        " VALUES (%s, %s, %s, 'contact', now())",
        outcome_id,
        tenant,
        lead,
    )
    return outcome_id


_TABLES = (
    ("core.signal", "id"),
    ("core.location", "id"),
    ("core.lead_outcome", "id"),
)


async def test_the_three_tables_hide_rows_without_context_and_across_tenants(
    db: Db, engines: dict[str, Any]
) -> None:
    from sqlalchemy import text

    from libs.common.tenant import set_tenant_context

    company = _company(db, TENANT)
    lead = _lead(db, TENANT, company)
    ids = {
        "core.signal": _fixture_signal(db, TENANT, company),
        "core.location": _fixture_location(db, TENANT, company),
        "core.lead_outcome": _fixture_outcome(db, TENANT, lead),
    }
    for table, column in _TABLES:
        query = text(f"SELECT {column} FROM {table} WHERE {column} = :id")  # noqa: S608
        params = {"id": ids[table]}
        async with engines["core"].connect() as connection:
            assert (await connection.execute(query, params)).all() == [], table
        async with engines["core"].connect() as connection:
            await set_tenant_context(connection, OTHER)
            assert (await connection.execute(query, params)).all() == [], table
        async with engines["core"].connect() as connection:
            await set_tenant_context(connection, TENANT)
            found = (await connection.execute(query, params)).all()
            assert [r[0] for r in found] == [ids[table]], table


async def test_the_three_tables_refuse_writes_without_the_matching_context(
    db: Db, engines: dict[str, Any]
) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from libs.common.tenant import set_tenant_context

    company = _company(db, TENANT)
    lead = _lead(db, TENANT, company)
    inserts = {
        "core.signal": (
            "INSERT INTO core.signal (tenant_id, company_id, source, source_external_id,"
            " signal_type, observed_at) VALUES (:t, :c, 'maps', :x, 'hiring', now())"
        ),
        "core.location": "INSERT INTO core.location (tenant_id, company_id, city)"
        " VALUES (:t, :c, 'Cali')",
        "core.lead_outcome": "INSERT INTO core.lead_outcome (tenant_id, lead_id, outcome,"
        " occurred_at) VALUES (:t, :l, 'contact', now())",
    }
    for table, sql_text in inserts.items():
        params = {"t": TENANT, "c": company, "l": lead, "x": uuid4().hex}
        for context in (None, OTHER):  # missing context, then the wrong tenant
            with pytest.raises(DBAPIError):
                async with engines["core"].begin() as connection:
                    if context is not None:
                        await set_tenant_context(connection, context)
                    await connection.execute(text(sql_text), params)
        async with engines["core"].begin() as connection:  # the right context writes
            await set_tenant_context(connection, TENANT)
            await connection.execute(text(sql_text), params)
        assert db.one(f"SELECT count(*) FROM {table} WHERE tenant_id = %s", TENANT)[0] >= 1, table


async def test_create_signal_writes_the_tenant_and_needs_a_visible_company(
    db: Db, engines: dict[str, Any]
) -> None:
    from libs.contracts.api import SignalCreate

    company = _company(db, TENANT)
    repository = _core_repository(db)

    def payload(tenant: UUID | None) -> Any:
        return SignalCreate(
            company_id=company,
            tenant_id=tenant,
            source="maps",
            signal_type="new_branch",
            observed_at=datetime.now(UTC),
            strength=0.5,
        )

    try:
        signal_id, event_id = await repository.create_signal(payload(TENANT), request_id=None)
        assert event_id is not None
        assert db.one("SELECT tenant_id FROM core.signal WHERE id = %s", signal_id) == (TENANT,)
        before = db.one("SELECT count(*) FROM core.signal WHERE company_id = %s", company)[0]
        # Without any context the tenant cannot be derived: the insert is refused, not orphaned.
        with pytest.raises(Exception):  # noqa: B017, PT011
            await repository.create_signal(payload(None), request_id=None)
        assert db.one("SELECT count(*) FROM core.signal WHERE company_id = %s", company) == (
            before,
        )
    finally:
        await repository.close()


async def test_record_outcome_runs_under_the_tenant_context(db: Db) -> None:
    from services.core.app.actions import LeadNotFoundError, OutcomeCreate, record_outcome

    company = _company(db, TENANT)
    lead = _lead(db, TENANT, company)
    repository = _core_repository(db)
    body = OutcomeCreate(outcome="contact", notes="called")
    try:
        result = await record_outcome(
            repository.action_unit_of_work(TENANT), lead, body, tenant_id=TENANT
        )
        assert result
        assert db.one(
            "SELECT tenant_id, outcome FROM core.lead_outcome WHERE lead_id = %s", lead
        ) == (TENANT, "contact")
        # Without the context the lead is invisible: 404 and nothing is written.
        with pytest.raises(LeadNotFoundError):
            await record_outcome(repository.action_unit_of_work(), lead, body, tenant_id=TENANT)
        with pytest.raises(LeadNotFoundError):
            await record_outcome(repository.action_unit_of_work(OTHER), lead, body, tenant_id=OTHER)
    finally:
        await repository.close()
    assert db.one("SELECT count(*) FROM core.lead_outcome WHERE lead_id = %s", lead) == (1,)


async def test_retention_delete_removes_the_three_tables_only_under_the_context(db: Db) -> None:
    company = _company(db, TENANT)
    lead = _lead(db, TENANT, company)
    _fixture_signal(db, TENANT, company)
    _fixture_location(db, TENANT, company)
    _fixture_outcome(db, TENANT, lead)
    repository = _core_repository(db)
    try:
        async with repository.company_unit_of_work() as tx:  # no context
            blind = await _delete_company_data(repository, tx, company)
        assert blind["signal"] == blind["location"] == blind["lead_outcome"] == 0
        async with repository.retention_unit_of_work() as tx:  # no context
            assert (await tx.delete_company_data(company))["signal"] == 0
        counts = await _retention_delete(repository, TENANT, company)
    finally:
        await repository.close()
    assert (counts["signal"], counts["location"], counts["lead_outcome"]) == (1, 1, 1)
    for table in ("core.signal", "core.location"):
        assert db.one(f"SELECT count(*) FROM {table} WHERE company_id = %s", company) == (0,)
    assert db.one("SELECT count(*) FROM core.lead_outcome WHERE lead_id = %s", lead) == (0,)


async def _delete_company_data(repository: Any, tx: Any, company: UUID) -> dict[str, int]:
    from services.core.app.repository import SqlRetentionTransaction

    return await SqlRetentionTransaction(tx.connection).delete_company_data(company)


async def _retention_delete(repository: Any, tenant: UUID, company: UUID) -> dict[str, int]:
    from services.core.app.repository import SqlRetentionUnitOfWork

    async with SqlRetentionUnitOfWork(repository.engine, tenant) as tx:
        return await tx.delete_company_data(company)


async def test_brief_facts_and_score_features_count_signals_only_under_the_context(
    db: Db,
) -> None:
    company = _company(db, TENANT)
    _fixture_signal(db, TENANT, company, "hiring")
    _fixture_signal(db, TENANT, company, "new_branch")
    repository = _core_repository(db)
    try:
        facts = await repository.brief_facts(company, TENANT)
        assert facts is not None and sorted(facts["signal_codes"]) == ["hiring", "new_branch"]
        assert await repository.brief_facts(company, OTHER) is None
        assert await repository.brief_facts(company, None) is None  # no context: all rows hidden
        mine = await repository.score_features(TENANT, 500, None)
        row = next(c for c in mine["companies"] if c["company_id"] == str(company))
        assert row["signal_strength"] > 0  # two signals counted
        theirs = await repository.score_features(OTHER, 500, None)
        assert all(c["company_id"] != str(company) for c in theirs["companies"])
    finally:
        await repository.close()


# --- T-179: validation inputs -----------------------------------------------------------


def _inputs_uow(engines: dict[str, Any], tenant: UUID) -> Any:
    from services.core.app.repository import SqlValidationInputUnitOfWork

    return SqlValidationInputUnitOfWork(engines["core"], tenant)


async def test_validation_inputs_write_one_key_audit_and_etag_as_core_app(
    db: Db, engines: dict[str, Any]
) -> None:
    from services.core.app.validation_inputs import (
        StaleInputsError,
        ValidationInputs,
        update_inputs,
    )

    project = _project(db, TENANT, metadata={"keep": "me", "version": 4})
    inputs = ValidationInputs.model_validate(
        {"growth": {"starting_mrr": 100, "new": 10}, "portfolio": {"npvs": [1, 2]}}
    )
    body = await update_inputs(
        _inputs_uow(engines, TENANT), TENANT, project, inputs, if_match=0, updated_by=uuid4()
    )
    assert body["version"] == 1
    keep, funnel_version, growth, version = db.one(
        "SELECT metadata->>'keep', metadata->>'version', (metadata->'validation'->'growth'->>'new')::float,"
        " metadata->'validation'->>'version' FROM core.project WHERE id = %s",
        project,
    )
    assert (keep, funnel_version, growth, version) == ("me", "4", 10.0, "1")
    action, tenant, sections = db.one(
        "SELECT action, tenant_id, payload->'sections' FROM core.audit_log WHERE aggregate_id = %s",
        project,
    )
    assert (action, tenant, sections) == (
        "validation.inputs_updated",
        TENANT,
        ["growth", "portfolio"],
    )
    with pytest.raises(StaleInputsError):
        await update_inputs(_inputs_uow(engines, TENANT), TENANT, project, inputs, if_match=0)
    assert db.one("SELECT count(*) FROM core.audit_log WHERE aggregate_id = %s", project) == (1,)


async def test_validation_inputs_of_another_tenant_project_are_not_found_and_not_written(
    db: Db, engines: dict[str, Any]
) -> None:
    from services.core.app.validation_inputs import (
        ProjectNotFoundError,
        ValidationInputs,
        update_inputs,
    )

    project = _project(db, OTHER, metadata={"keep": "me"})
    inputs = ValidationInputs.model_validate({"growth": {"new": 1}})
    with pytest.raises(ProjectNotFoundError):
        await update_inputs(_inputs_uow(engines, TENANT), TENANT, project, inputs, if_match=0)
    assert db.one("SELECT metadata FROM core.project WHERE id = %s", project) == ({"keep": "me"},)
    assert db.one("SELECT count(*) FROM core.audit_log WHERE aggregate_id = %s", project) == (0,)


# --- T-181: core.lead.project_id (D-26) --------------------------------------------------


async def test_conversion_sets_lead_project_id_only_under_the_tenant_context(
    db: Db, engines: dict[str, Any]
) -> None:
    from sqlalchemy import text

    from libs.common.tenant import set_tenant_context
    from services.core.app.repository import SqlActionTransaction

    company = _company(db, TENANT)
    lead = _lead(db, TENANT, company)
    assert db.one("SELECT project_id FROM core.lead WHERE id = %s", lead) == (None,)
    row: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": TENANT,
        "lead_id": lead,
        "name": "Pilot",
        "service_line": "x",
        "hypothesis": "h",
        "status": "draft",
        "estimated_roi": None,
        "metadata": {},
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    # Without the context RLS hides the lead: the project insert is refused.
    with pytest.raises(Exception, match="row-level security"):
        async with engines["core"].begin() as connection:
            await SqlActionTransaction(connection).insert_project(row)
    async with engines["core"].begin() as connection:
        await set_tenant_context(connection, TENANT)
        await SqlActionTransaction(connection).insert_project(row)
    assert db.one("SELECT project_id FROM core.lead WHERE id = %s", lead) == (row["id"],)
    query = text("SELECT count(*) FROM core.lead WHERE project_id = :p")
    async with engines["core"].connect() as connection:
        assert (await connection.execute(query, {"p": row["id"]})).scalar_one() == 0
    async with engines["core"].connect() as connection:
        await set_tenant_context(connection, TENANT)
        assert (await connection.execute(query, {"p": row["id"]})).scalar_one() == 1


# --- T-191: opt-out and retention purge are tenant-aware (D-36), proven as core_app -----------


def _aged_company(db: Db, tenant: UUID, nit: str, days: int) -> UUID:
    company = _company(db, tenant)
    db.rows(
        "UPDATE core.company SET nit = %s, created_at = now() - make_interval(days => %s)"
        " WHERE id = %s",
        nit,
        days,
        company,
    )
    return company


def _company_rows(db: Db, company: UUID) -> int:
    return int(db.one("SELECT count(*) FROM core.company WHERE id = %s", company)[0])


async def test_opt_out_of_tenant_a_deletes_only_its_rows_as_core_app(db: Db) -> None:
    from services.core.app.retention import OptOutRequest, opt_out

    in_a = _aged_company(db, TENANT, "9001", 1)
    in_b = _aged_company(db, OTHER, "9001", 1)  # same NIT, other tenant
    lead_a, lead_b = _lead(db, TENANT, in_a), _lead(db, OTHER, in_b)
    for tenant, company, lead in ((TENANT, in_a, lead_a), (OTHER, in_b, lead_b)):
        _fixture_signal(db, tenant, company)
        _fixture_location(db, tenant, company)
        _fixture_outcome(db, tenant, lead)
    repository = _core_repository(db)
    try:
        # No context: every row is hidden, so an existing company is simply not found.
        blind = await opt_out(repository.retention_unit_of_work(), OptOutRequest(company_id=in_a))
        assert blind["status"] == "already_removed" and _company_rows(db, in_a) == 1
        # Tenant B cannot delete the company of tenant A, by id or by NIT.
        cross = await opt_out(
            repository.retention_unit_of_work(OTHER), OptOutRequest(company_id=in_a)
        )
        assert cross["status"] == "already_removed" and _company_rows(db, in_a) == 1
        done = await opt_out(repository.retention_unit_of_work(TENANT), OptOutRequest(nit="9001"))
        assert done["status"] == "removed" and done["deleted"]["company"] == 1
        assert done["deleted"]["signal"] == done["deleted"]["location"] == 1
        # A blind delete of a company that exists must fail loudly, never report success.
        async with repository.retention_unit_of_work() as tx:
            assert (await tx.delete_company_data(in_b))["company"] == 0
    finally:
        await repository.close()
    assert _company_rows(db, in_a) == 0
    assert db.one("SELECT count(*) FROM core.signal WHERE company_id = %s", in_a) == (0,)
    assert _company_rows(db, in_b) == 1
    assert db.one("SELECT count(*) FROM core.signal WHERE company_id = %s", in_b) == (1,)
    assert db.one("SELECT count(*) FROM core.location WHERE company_id = %s", in_b) == (1,)
    assert db.one("SELECT count(*) FROM core.lead_outcome WHERE lead_id = %s", lead_b) == (1,)
    tenant, payload = db.one(
        "SELECT tenant_id, payload::text FROM core.audit_log WHERE action = 'company.opt_out'"
        " AND tenant_id = %s",
        TENANT,
    )
    assert tenant == TENANT and "9001" not in payload and str(in_a) not in payload


async def test_retention_purge_runs_each_tenant_separately_as_core_app(db: Db) -> None:
    from services.core.app.retention import purge_inactive_companies, purge_tenants

    old_a = _aged_company(db, TENANT, "1", 500)
    new_a = _aged_company(db, TENANT, "2", 5)
    old_b = _aged_company(db, OTHER, "3", 500)
    _fixture_signal(db, TENANT, old_a)
    db.rows(
        "UPDATE core.signal SET observed_at = now() - interval '500 days' WHERE company_id = %s",
        old_a,
    )
    repository = _core_repository(db)
    try:
        # Without a context nothing is a candidate: the job would silently purge nothing.
        blind = await purge_inactive_companies(repository.retention_unit_of_work())
        assert blind["candidates"] == 0 and _company_rows(db, old_a) == 1
        dry = await purge_tenants(repository.retention_unit_of_work, [TENANT, OTHER], dry_run=True)
        assert dry["per_tenant"][str(TENANT)]["candidates"] >= 1 and dry["purged"] == 0
        assert _company_rows(db, old_a) == _company_rows(db, old_b) == 1
        only_a = await purge_tenants(repository.retention_unit_of_work, [TENANT])
        assert only_a["failed"] == [] and _company_rows(db, old_a) == 0
        assert _company_rows(db, old_b) == 1  # tenant B untouched while only A ran
        both = await purge_tenants(repository.retention_unit_of_work, [TENANT, OTHER])
        assert both["failed"] == [] and both["per_tenant"][str(OTHER)]["purged"] >= 1
    finally:
        await repository.close()
    assert _company_rows(db, old_b) == 0 and _company_rows(db, new_a) == 1
    assert db.one("SELECT count(*) FROM core.signal WHERE company_id = %s", old_a) == (0,)
    assert (
        db.one(
            "SELECT count(*) FROM core.audit_log"
            " WHERE action = 'retention.purge' AND tenant_id = %s",
            OTHER,
        )[0]
        >= 1
    )
