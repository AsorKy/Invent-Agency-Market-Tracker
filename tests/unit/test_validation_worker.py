"""Validation worker: formulas from core data, writes, stop condition, tenant context (T-147)."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic_settings")
from support_cov_services import FakeEngine, FakeResult  # noqa: E402

from services.core.app import validation_worker as worker  # noqa: E402
from services.core.app.validation_reads import TENANT_WIDE_PROJECT  # noqa: E402

TENANT = UUID("11111111-1111-1111-1111-111111111111")
PROJECT = UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 10, 22, tzinfo=UTC)
INPUTS = {
    "financial": {
        "discount_rate": 0.1,
        "cash_flows": [-100, 110],
        "horizon": "H1",
        "outcomes": [[0.5, 100], [0.5, 0]],
    },
    "unit_economics": {
        "acquisition_cost": 1000,
        "new_customers": 10,
        "arpa": 100,
        "revenue": 200,
        "cogs": 50,
        "monthly_churn": 0.05,
    },
    "funnel": {"avg_deal_size": 1000, "quota": 100, "pipeline_value": 350},
    "growth": {
        "starting_mrr": 100,
        "expansion": 20,
        "contraction": 5,
        "churned": 5,
        "new": 10,
        "growth_pct": 30,
        "profit_margin_pct": 15,
        "net_burn": 50,
        "net_new_arr": 25,
        "delta_arr": 40,
        "prior_quarter_sm": 20,
    },
    "portfolio": {"revenue_by_horizon": {"H1": 60, "H2": 30, "H3": 10}, "npvs": [10, 20, 30]},
}


class Store:
    def __init__(self, projects: list[dict[str, Any]], tenants_seen: list[UUID]) -> None:
        self._projects, self.rows, self.tenants_seen = projects, [], tenants_seen
        self.committed: list[tuple[str, dict[str, Any]]] = []
        self.counts = {"entered": 10, "converted": 5, "won": 3, "lost": 1}
        self.scores = [(0.9, True), (0.8, True), (0.2, False), (0.1, False)]
        self.holdout = {False: {"won": 3, "lost": 1}, True: {"won": 1, "lost": 1}}
        self.fail = False
        self.project_calls: list[UUID] = []
        self.line_calls: list[str | None] = []
        self.line_counts = {"entered": 20, "converted": 4, "won": 1, "lost": 1}

    async def __aenter__(self) -> "Store":
        self.rows = []
        return self

    async def __aexit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type is None:
            self.committed += self.rows

    async def projects(self, tenant_id: UUID, limit: int) -> list[dict[str, Any]]:
        self.tenants_seen.append(tenant_id)
        if self.fail:
            raise RuntimeError("boom")
        return self._projects[:limit]

    async def lead_counts(self, tenant_id: UUID, project_id: UUID) -> dict[str, int]:
        self.project_calls.append(project_id)
        return self.counts

    async def won_cycle_days(self, tenant_id: UUID, project_id: UUID) -> list[float]:
        return [10.0, 20.0, 30.0] if self.counts else []

    async def service_line_counts(
        self, tenant_id: UUID, service_line: str | None
    ) -> dict[str, int]:
        self.line_calls.append(service_line)
        return self.line_counts

    async def service_line_cycle_days(
        self, tenant_id: UUID, service_line: str | None
    ) -> list[float]:
        return [5.0] if self.line_counts else []

    async def closed_scores(self, tenant_id: UUID) -> list[tuple[float, bool]]:
        return self.scores

    async def holdout_counts(self, tenant_id: UUID) -> dict[bool, dict[str, int]]:
        return self.holdout

    async def insert(self, table: str, row: dict[str, Any]) -> None:
        self.rows.append((table, row))


def _store(inputs: Any = INPUTS) -> Store:
    return Store([{"id": PROJECT, "service_line": None, "inputs": inputs}], [])


async def test_all_cards_are_computed_and_written() -> None:
    store = _store()
    result = await worker.run_tenant(lambda _t: store, TENANT, now=NOW)
    assert result == {"projects": 1, "written": 7, "skipped": 0}
    rows = dict(
        (t, r)
        for t, r in store.committed
        if not (t == "funnel_metric" and r["project_id"] == TENANT_WIDE_PROJECT)
    )
    assert float(rows["financial_run"]["npv"]) == pytest.approx(0.0)
    assert float(rows["financial_run"]["irr"]) == pytest.approx(0.10)
    assert json.loads(rows["financial_run"]["assumptions"])["horizon"] == "H1"
    assert rows["unit_economics"]["ltv_cac_ratio"] == Decimal("15.0000")
    assert rows["funnel_metric"]["conversion_rate"] == Decimal("0.500000")
    assert rows["funnel_metric"]["win_rate"] == Decimal("0.750000")
    assert rows["funnel_metric"]["cycle_time_days"] == Decimal("20.00")
    assert rows["funnel_metric"]["coverage"] == Decimal("3.5000")
    assert rows["growth_metric"]["nrr"] == Decimal("1.100000")
    assert rows["growth_metric"]["magic_number"] == Decimal("2.0000")
    assert json.loads(rows["portfolio_snapshot"]["horizon_mix"])["H1"] == pytest.approx(0.6)
    assert rows["portfolio_snapshot"]["sharpe_like"] == Decimal("2.000000")
    meta = rows["meta_ml_metric"]
    assert meta["project_id"] == TENANT_WIDE_PROJECT and meta["psi"] is None
    assert meta["precision_at_k"] == Decimal("0.500000")
    assert meta["funnel_lift"] == Decimal("1.500000")
    assert all(r["tenant_id"] == TENANT for _, r in store.committed)
    assert all(r["measured_at"] == NOW for _, r in store.committed)


async def test_funnel_is_per_project_and_tenant_row_counts_all_leads() -> None:
    store = _store()
    await worker.run_tenant(lambda _t: store, TENANT, now=NOW)
    assert store.project_calls == [PROJECT] and store.line_calls == [None]
    funnel = [r for t, r in store.committed if t == "funnel_metric"]
    by_project = {r["project_id"]: r for r in funnel}
    assert by_project[PROJECT]["conversion_rate"] == Decimal("0.500000")
    assert by_project[TENANT_WIDE_PROJECT]["conversion_rate"] == Decimal("0.200000")
    assert by_project[TENANT_WIDE_PROJECT]["cycle_time_days"] == Decimal("5.00")


async def test_project_without_leads_has_an_empty_funnel() -> None:
    store = _store({})
    store.counts = {}
    store.scores, store.holdout, store.line_counts = [], {}, {}
    await worker.run_tenant(lambda _t: store, TENANT, now=NOW)
    assert store.committed == []


async def test_missing_inputs_leave_cards_unwritten() -> None:
    store = _store({})
    store.scores, store.holdout = [], {}
    result = await worker.run_tenant(lambda _t: store, TENANT, now=NOW)
    tables = [t for t, _ in store.committed]
    assert tables == ["funnel_metric", "funnel_metric"] and result["skipped"] == 5


async def test_garbage_inputs_never_raise() -> None:
    bad = {
        "financial": {"discount_rate": "x", "cash_flows": [True]},
        "unit_economics": {"acquisition_cost": float("nan"), "new_customers": 0},
        "growth": {"starting_mrr": 0},
        "portfolio": {"revenue_by_horizon": {"H1": "a"}, "npvs": "no"},
    }
    store = _store(json.dumps(bad))
    await worker.run_tenant(lambda _t: store, TENANT, now=NOW)
    assert {t for t, _ in store.committed} <= {"funnel_metric", "meta_ml_metric", "growth_metric"}


def test_fit_rounds_and_rejects_overflow() -> None:
    assert worker.fit("funnel_metric", "win_rate", 0.123456789) == Decimal("0.123457")
    assert worker.fit("financial_run", "irr", 12345.0) is None
    assert worker.fit("financial_run", "irr", None) is None
    assert worker.fit("financial_run", "irr", float("inf")) is None


async def test_failed_transaction_commits_nothing() -> None:
    store = _store()
    store.fail = True
    results = await worker.run_passes(lambda _t: store, [TENANT])
    assert results == [{"tenant_id": str(TENANT), "error": True}]
    assert store.committed == []


async def test_passes_are_the_stop_condition() -> None:
    store = _store()
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    other = uuid4()
    results = await worker.run_passes(
        lambda _t: store, [TENANT, other], passes=3, interval_seconds=5, sleep=sleep
    )
    assert len(results) == 6 and sleeps == [5, 5]
    assert await worker.run_passes(lambda _t: store, [TENANT], passes=0) == []


async def test_sql_store_sets_tenant_before_any_statement() -> None:
    engine = FakeEngine([FakeResult(), FakeResult()])
    factory = worker.sql_store_factory(engine)  # type: ignore[arg-type]
    async with factory(TENANT) as store:
        await store.projects(TENANT, 5)
        await store.insert(
            "funnel_metric",
            {
                "tenant_id": TENANT,
                "project_id": PROJECT,
                "source_version": "v",
                "measured_at": NOW,
            },
        )
    statements = [call[0] for call in engine.connection.calls]
    assert "set_config('app.tenant_id'" in statements[0]
    assert "FROM core.project" in statements[1]
    assert "INSERT INTO validation.funnel_metric" in statements[2]


async def test_sql_store_funnel_reads_filter_by_project_not_service_line() -> None:
    engine = FakeEngine([FakeResult(), FakeResult(), FakeResult()])
    factory = worker.sql_store_factory(engine)  # type: ignore[arg-type]
    async with factory(TENANT) as store:
        await store.lead_counts(TENANT, PROJECT)
        await store.won_cycle_days(TENANT, PROJECT)
    statements = [call[0] for call in engine.connection.calls]
    assert "set_config('app.tenant_id'" in statements[0]
    assert all("project_id = :project_id" in s and "service_line" not in s for s in statements[1:])


def test_sql_store_needs_a_database() -> None:
    with pytest.raises(RuntimeError):
        worker.sql_store_factory(None)


def test_parser_requires_a_tenant_and_main_entry_exists() -> None:
    with pytest.raises(SystemExit):
        worker.build_parser().parse_args([])
    args = worker.build_parser().parse_args(["--tenant", str(TENANT)])
    assert args.tenant == [TENANT] and args.passes == 1


def test_run_without_database_returns_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from libs.common import settings

    settings.get_settings.cache_clear()
    try:
        assert asyncio.run(worker.run(["--tenant", str(TENANT)])) == 2
    finally:
        settings.get_settings.cache_clear()
