"""T-145: core stores the score from lead.score_updated on core.company (no ml reads).

Offline checks: the migration shape, the SQL of the reads and of the guarded write, and the
worker handler. Behaviour of the version guard is asserted on the SQL text because no database
runs here; ``tests/integration`` is the database tier.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine

from services.core.app import repository as repo
from services.core.app import worker
from services.core.app.brief_facts import read_brief_facts
from tests.unit.support_cov import FakeEngine, FakeResult

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 22, tzinfo=UTC)
TENANT = uuid4()
COMPANY = uuid4()


def _repository(results: list[FakeResult] | None = None) -> tuple[repo.CoreRepository, FakeEngine]:
    repository = repo.CoreRepository(None)
    engine = FakeEngine(results)
    repository.engine = cast(AsyncEngine, engine)
    return repository, engine


def test_migration_adds_nullable_columns_and_keeps_company_rls() -> None:
    sql = (ROOT / "migrations/20261022_0012_core_company_score.sql").read_text()
    for column in ("score integer", "score_source_version text", "score_updated_at timestamptz"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql
    assert "BETWEEN 0 AND 100" in sql
    assert "SET NOT NULL" not in sql and "DROP" not in sql
    # Row level security is table level: company_tenant_policy covers the new columns.
    assert "CREATE POLICY" not in sql


async def test_store_score_sets_tenant_then_writes_with_the_version_guard() -> None:
    repository, engine = _repository([FakeResult(), FakeResult(rowcount=1)])
    changed = await repository.store_company_score(
        company_id=COMPANY, tenant_id=TENANT, score=80, source_version="7", now=NOW
    )
    assert changed is True
    set_sql, _ = engine.connection.calls[0]
    assert "set_config" in set_sql
    sql, params = engine.connection.calls[1]
    assert "UPDATE core.company" in sql
    assert "ml." not in sql
    assert "tenant_id IS NOT DISTINCT FROM :tenant_id" in sql
    assert "score_source_version IS NULL" in sql
    assert "< CAST(CAST(:source_version AS text) AS bigint)" in sql
    assert params["score"] == 80 and params["source_version"] == "7"


async def test_store_score_without_tenant_skips_context() -> None:
    repository, engine = _repository([FakeResult(rowcount=0)])
    changed = await repository.store_company_score(
        company_id=COMPANY, tenant_id=None, score=10, source_version=None, now=NOW
    )
    assert changed is False
    assert len(engine.connection.calls) == 1


async def test_reads_use_the_stored_column_and_min_score_filters_by_it() -> None:
    repository, engine = _repository([FakeResult(), FakeResult([])])
    await repository.list_leads(5, min_score=60, tenant_id=TENANT)
    sql = engine.connection.calls[1][0]
    assert "c.score >= :min_score" in sql
    assert "CAST(NULL" not in sql

    repository, engine = _repository([FakeResult(), FakeResult([])])
    assert await repository.get_lead(uuid4(), TENANT) is None
    assert "c.score" in engine.connection.calls[1][0]

    engine = FakeEngine([FakeResult(), FakeResult([])])
    assert await read_brief_facts(cast(AsyncEngine, engine), COMPANY, TENANT) is None
    assert "c.score" in engine.connection.calls[1][0]


async def test_handler_stores_score_then_qualifies(monkeypatch: Any) -> None:
    order: list[str] = []

    async def fake_handle(uow: Any, fields: Any, on_hot: Any = None) -> None:
        order.append("qualify")

    async def store(**kwargs: Any) -> bool:
        order.append("store")
        order.append(str(kwargs["tenant_id"]))
        order.append(str(kwargs["source_version"]))
        assert kwargs["score"] == 77 and kwargs["company_id"] == COMPANY
        return True

    monkeypatch.setattr(worker, "handle_score_event", fake_handle)
    handlers = worker.build_handlers(lambda: cast(Any, None), None, store)
    await handlers["lead.score_updated"](
        {
            "event_id": str(uuid4()),
            "aggregate_id": str(COMPANY),
            "score": "77",
            "tenant_id": str(TENANT),
            "source_version": "9",
        }
    )
    assert order == ["store", str(TENANT), "9", "qualify"]


async def test_handler_without_tenant_or_version_passes_none(monkeypatch: Any) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_handle(uow: Any, fields: Any, on_hot: Any = None) -> None:
        return None

    async def store(**kwargs: Any) -> bool:
        seen.append(kwargs)
        return False

    monkeypatch.setattr(worker, "handle_score_event", fake_handle)
    handlers = worker.build_handlers(lambda: cast(Any, None), None, store)
    await handlers["lead.score_updated"](
        {"event_id": str(uuid4()), "aggregate_id": str(COMPANY), "score": "5"}
    )
    assert seen[0]["tenant_id"] is None and seen[0]["source_version"] is None
