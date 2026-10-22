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


# --- T-168: qualification applies the tenant of the score event -------------------------


def _score_event(
    company: UUID, score: int, tenant: UUID | None, event_id: UUID | None = None
) -> Any:
    fields = {
        "event_id": str(event_id or uuid4()),
        "event_type": "lead.score_updated",
        "aggregate_id": str(company),
        "score": str(score),
    }
    if tenant is not None:
        fields["tenant_id"] = str(tenant)
    return fields


async def _deliver(db: Db, fields: Any) -> None:
    from services.core.app.worker import build_handlers

    repository = _core_repository(db)
    try:
        handlers = build_handlers(repository.qualification_unit_of_work)
        await asyncio.wait_for(handlers["lead.score_updated"](fields), timeout=30)
    finally:
        await repository.close()


_LEADS = "SELECT tenant_id, company_id, stage, version FROM core.lead WHERE company_id = %s"


async def test_score_event_creates_then_advances_the_lead_of_a_tenant_company(db: Db) -> None:
    company = _company(db, TENANT)
    await _deliver(db, _score_event(company, 10, TENANT))
    assert db.one(_LEADS, company) == (TENANT, company, "discovered", 1)
    await _deliver(db, _score_event(company, 95, TENANT))
    assert db.one(_LEADS, company) == (TENANT, company, "hot", 3)


async def test_second_delivery_of_the_same_score_event_changes_nothing(db: Db) -> None:
    company = _company(db, TENANT)
    fields = _score_event(company, 95, TENANT)
    await _deliver(db, fields)
    first = db.one(_LEADS, company)
    audits = db.one("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s", TENANT)
    await _deliver(db, fields)
    assert db.one(_LEADS, company) == first
    assert db.one("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s", TENANT) == audits


async def test_score_event_carrying_another_tenant_creates_no_lead(db: Db) -> None:
    company = _company(db, TENANT)
    await _deliver(db, _score_event(company, 95, OTHER))
    assert db.one("SELECT count(*) FROM core.lead WHERE company_id = %s", company) == (0,)
