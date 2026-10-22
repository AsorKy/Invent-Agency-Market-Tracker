"""Compute validation metrics V1 to V6 per tenant and project and store them (T-147).

Run once or for a bounded number of passes with
``python -m services.core.app.validation_worker --tenant <uuid> [--tenant ...]`` (not part of
Compose). Stop condition: ``--passes`` (default 1) passes over the given tenants, each pass
handling at most ``--limit`` projects per tenant; a failing tenant is logged and the run goes on.

Inputs come from ``core`` only (INV-01: no ``ml`` or ``pulse`` read): the project row, the
optional ``metadata.validation`` object that a strategist fills (cash flows, unit economics,
growth and portfolio figures) and ``core.lead`` and ``core.company`` for the funnel and the
meta-ML metrics. A metric whose input is missing stays null; a card without any value is not
written. Every tenant is handled in one transaction that calls ``set_tenant_context`` first.
The formulas are the pure functions of ``libs/domain/validation.py``; the V6 row is tenant wide
(``TENANT_WIDE_PROJECT``). Reads are in ``validation_reads.py``. O(projects + closed leads).
"""

import argparse
import asyncio
import json
import logging
import math
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from libs.common.tenant import set_tenant_context
from libs.domain import validation as v
from services.core.app.validation_reads import TENANT_WIDE_PROJECT

_log = logging.getLogger(__name__)

PRODUCER_VERSION = "validation-worker-1"
DEFAULT_LIMIT = 200
CLOSED_LEAD_LIMIT = 1000
TOP_K = 10
# Stages from which a lead counts as converted past the first gate.
CONVERTED_STAGES = ("contacted", "meeting", "proposal", "won")

# Column -> (integer digits, scale) of the numeric type in migration 0009.
_FIT: dict[str, dict[str, tuple[int, int]]] = {
    "financial_run": {
        "discount_rate": (2, 6),
        "npv": (16, 2),
        "irr": (4, 6),
        "payback_periods": (8, 2),
        "discounted_payback_periods": (8, 2),
        "emv": (16, 2),
    },
    "unit_economics": {
        "cac": (16, 2),
        "ltv": (16, 2),
        "ltv_cac_ratio": (8, 4),
        "cac_payback_months": (8, 2),
        "gross_margin": (2, 6),
    },
    "funnel_metric": {
        "conversion_rate": (2, 6),
        "win_rate": (2, 6),
        "cycle_time_days": (8, 2),
        "velocity": (14, 4),
        "coverage": (6, 4),
    },
    "growth_metric": {
        "mrr": (16, 2),
        "nrr": (4, 6),
        "grr": (4, 6),
        "rule_of_40": (6, 4),
        "burn_multiple": (8, 4),
        "magic_number": (8, 4),
    },
    "portfolio_snapshot": {"hhi": (2, 6), "sharpe_like": (6, 6)},
    "meta_ml_metric": {
        "model_utility": (6, 6),
        "decision_latency_days": (8, 2),
        "psi": (4, 6),
        "precision_at_k": (2, 6),
        "funnel_lift": (6, 6),
    },
}


_PROJECTS = """
    SELECT id, service_line, COALESCE(metadata->'validation', '{}'::jsonb) AS inputs
    FROM core.project
    WHERE tenant_id = :tenant_id
    ORDER BY id
    LIMIT :limit
"""

_LEAD_COUNTS = """
    SELECT count(*) AS entered,
           count(*) FILTER (WHERE stage IN ('contacted', 'meeting', 'proposal', 'won'))
               AS converted,
           count(*) FILTER (WHERE stage = 'won') AS won,
           count(*) FILTER (WHERE stage = 'lost') AS lost
    FROM core.lead
    WHERE tenant_id = :tenant_id
      AND (CAST(:service_line AS text) IS NULL OR service_line = :service_line)
"""

_WON_CYCLE_DAYS = """
    SELECT CAST(EXTRACT(EPOCH FROM (last_transition_at - created_at)) / 86400.0
                AS double precision) AS days
    FROM core.lead
    WHERE tenant_id = :tenant_id AND stage = 'won' AND last_transition_at IS NOT NULL
      AND (CAST(:service_line AS text) IS NULL OR service_line = :service_line)
    ORDER BY last_transition_at DESC
    LIMIT :limit
"""

_CLOSED_SCORES = """
    SELECT CAST(c.score AS double precision) AS score, (l.stage = 'won') AS won
    FROM core.lead l
    JOIN core.company c ON c.id = l.company_id
    WHERE l.tenant_id = :tenant_id AND l.stage IN ('won', 'lost') AND c.score IS NOT NULL
    ORDER BY l.last_transition_at DESC NULLS LAST, l.id
    LIMIT :limit
"""

_HOLDOUT_COUNTS = """
    SELECT holdout,
           count(*) FILTER (WHERE stage = 'won') AS won,
           count(*) FILTER (WHERE stage = 'lost') AS lost
    FROM core.lead
    WHERE tenant_id = :tenant_id AND holdout IS NOT NULL
    GROUP BY holdout
"""


class ValidationStore(Protocol):
    """What ``run_tenant`` needs; the SQL class below and the test fake implement it."""

    async def __aenter__(self) -> "ValidationStore": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def projects(self, tenant_id: UUID, limit: int) -> list[dict[str, Any]]: ...
    async def lead_counts(self, tenant_id: UUID, service_line: str | None) -> dict[str, int]: ...
    async def won_cycle_days(self, tenant_id: UUID, service_line: str | None) -> list[float]: ...
    async def closed_scores(self, tenant_id: UUID) -> list[tuple[float, bool]]: ...
    async def holdout_counts(self, tenant_id: UUID) -> dict[bool, dict[str, int]]: ...
    async def insert(self, table: str, row: dict[str, Any]) -> None: ...


class SqlValidationStore:
    """One transaction for one tenant; the tenant context is set before any statement."""

    def __init__(self, engine: AsyncEngine, tenant_id: UUID) -> None:
        self._context = engine.begin()
        self._tenant_id = tenant_id
        self.connection: AsyncConnection

    async def __aenter__(self) -> "SqlValidationStore":
        self.connection = await self._context.__aenter__()
        try:
            await set_tenant_context(self.connection, self._tenant_id)
        except BaseException:
            await self._context.__aexit__(*sys.exc_info())
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._context.__aexit__(exc_type, exc, tb)

    async def projects(self, tenant_id: UUID, limit: int) -> list[dict[str, Any]]:
        result = await self.connection.execute(
            text(_PROJECTS), {"tenant_id": tenant_id, "limit": limit}
        )
        return [dict(row) for row in result.mappings().all()]

    async def lead_counts(self, tenant_id: UUID, service_line: str | None) -> dict[str, int]:
        result = await self.connection.execute(
            text(_LEAD_COUNTS), {"tenant_id": tenant_id, "service_line": service_line}
        )
        row = result.mappings().first()
        return {k: int(row[k] or 0) for k in ("entered", "converted", "won", "lost")} if row else {}

    async def won_cycle_days(self, tenant_id: UUID, service_line: str | None) -> list[float]:
        result = await self.connection.execute(
            text(_WON_CYCLE_DAYS),
            {"tenant_id": tenant_id, "service_line": service_line, "limit": CLOSED_LEAD_LIMIT},
        )
        return [float(row["days"]) for row in result.mappings().all() if row["days"] is not None]

    async def closed_scores(self, tenant_id: UUID) -> list[tuple[float, bool]]:
        result = await self.connection.execute(
            text(_CLOSED_SCORES), {"tenant_id": tenant_id, "limit": CLOSED_LEAD_LIMIT}
        )
        return [(float(row["score"]), bool(row["won"])) for row in result.mappings().all()]

    async def holdout_counts(self, tenant_id: UUID) -> dict[bool, dict[str, int]]:
        result = await self.connection.execute(text(_HOLDOUT_COUNTS), {"tenant_id": tenant_id})
        return {
            bool(row["holdout"]): {"won": int(row["won"]), "lost": int(row["lost"])}
            for row in result.mappings().all()
        }

    async def insert(self, table: str, row: dict[str, Any]) -> None:
        sql: str
        if table == "financial_run":
            sql = """
        INSERT INTO validation.financial_run
            (tenant_id, project_id, source_version, measured_at, discount_rate, npv, irr,
             payback_periods, discounted_payback_periods, emv, assumptions)
        VALUES
            (:tenant_id, :project_id, :source_version, :measured_at, :discount_rate, :npv, :irr,
             :payback_periods, :discounted_payback_periods, :emv, CAST(:assumptions AS jsonb))
    """
        elif table == "unit_economics":
            sql = """
        INSERT INTO validation.unit_economics
            (tenant_id, project_id, source_version, measured_at, cac, ltv, ltv_cac_ratio,
             cac_payback_months, gross_margin)
        VALUES
            (:tenant_id, :project_id, :source_version, :measured_at, :cac, :ltv, :ltv_cac_ratio,
             :cac_payback_months, :gross_margin)
    """
        elif table == "funnel_metric":
            sql = """
        INSERT INTO validation.funnel_metric
            (tenant_id, project_id, source_version, measured_at, conversion_rate, win_rate,
             cycle_time_days, velocity, coverage)
        VALUES
            (:tenant_id, :project_id, :source_version, :measured_at, :conversion_rate, :win_rate,
             :cycle_time_days, :velocity, :coverage)
    """
        elif table == "growth_metric":
            sql = """
        INSERT INTO validation.growth_metric
            (tenant_id, project_id, source_version, measured_at, mrr, nrr, grr, rule_of_40,
             burn_multiple, magic_number)
        VALUES
            (:tenant_id, :project_id, :source_version, :measured_at, :mrr, :nrr, :grr,
             :rule_of_40, :burn_multiple, :magic_number)
    """
        elif table == "portfolio_snapshot":
            sql = """
        INSERT INTO validation.portfolio_snapshot
            (tenant_id, project_id, source_version, measured_at, horizon_mix, hhi, sharpe_like)
        VALUES
            (:tenant_id, :project_id, :source_version, :measured_at,
             CAST(:horizon_mix AS jsonb), :hhi, :sharpe_like)
    """
        elif table == "meta_ml_metric":
            sql = """
        INSERT INTO validation.meta_ml_metric
            (tenant_id, project_id, source_version, measured_at, model_utility,
             decision_latency_days, psi, precision_at_k, funnel_lift)
        VALUES
            (:tenant_id, :project_id, :source_version, :measured_at, :model_utility,
             :decision_latency_days, :psi, :precision_at_k, :funnel_lift)
    """
        else:
            raise KeyError(table)
        await self.connection.execute(text(sql), row)


StoreFactory = Callable[[UUID], ValidationStore]


def sql_store_factory(engine: AsyncEngine | None) -> StoreFactory:
    if engine is None:
        raise RuntimeError("DATABASE_URL is required for core writes")
    return lambda tenant_id: SqlValidationStore(engine, tenant_id)


# ------------------------------------------------------------------ pure helpers


def _num(source: Mapping[str, Any], key: str) -> float | None:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _series(source: Mapping[str, Any], key: str) -> list[float] | None:
    value = source.get(key)
    if not isinstance(value, list) or not value:
        return None
    if any(isinstance(x, bool) or not isinstance(x, int | float) for x in value):
        return None
    return [float(x) for x in value]


def fit(table: str, column: str, value: float | None) -> Decimal | None:
    """Round to the scale of the column; None when null, not finite or too wide for it."""
    if value is None or not math.isfinite(value):
        return None
    digits, scale = _FIT[table][column]
    rounded = Decimal(repr(float(value))).quantize(
        Decimal(1).scaleb(-scale), rounding=ROUND_HALF_EVEN
    )
    return None if abs(rounded) >= Decimal(10) ** digits else rounded


def _financial(inputs: Mapping[str, Any]) -> dict[str, Any]:
    source = inputs.get("financial")
    if not isinstance(source, dict):
        return {}
    rate, flows = _num(source, "discount_rate"), _series(source, "cash_flows")
    if rate is None or flows is None:
        return {}
    pairs = source.get("outcomes")
    outcomes = (
        [(float(p), float(x)) for p, x in pairs]
        if isinstance(pairs, list)
        and all(isinstance(i, list | tuple) and len(i) == 2 for i in pairs)
        else []
    )
    horizon = source.get("horizon")
    return {
        "discount_rate": rate,
        "npv": v.npv(rate, flows),
        "irr": v.irr(flows),
        "payback_periods": v.payback_period(flows),
        "discounted_payback_periods": v.discounted_payback_period(rate, flows),
        "emv": v.emv(outcomes),
        "assumptions": {
            "cash_flows": flows,
            "horizon": horizon if horizon in ("H1", "H2", "H3") else None,
        },
    }


def _unit_economics(inputs: Mapping[str, Any]) -> dict[str, Any]:
    source = inputs.get("unit_economics")
    if not isinstance(source, dict):
        return {}
    cost, customers = _num(source, "acquisition_cost"), _num(source, "new_customers")
    arpa, revenue = _num(source, "arpa"), _num(source, "revenue")
    cogs, churn = _num(source, "cogs"), _num(source, "monthly_churn")
    cac = v.cac(cost, customers) if cost is not None and customers is not None else None
    margin = v.gross_margin(revenue, cogs) if revenue is not None and cogs is not None else None
    ltv = payback = None
    if arpa is not None and margin is not None:
        ltv = v.ltv(arpa, margin, churn) if churn is not None else None
        payback = v.cac_payback_months(cac, arpa, margin) if cac is not None else None
    return {
        "cac": cac,
        "ltv": ltv,
        "ltv_cac_ratio": v.ltv_cac_ratio(ltv, cac),
        "cac_payback_months": payback,
        "gross_margin": margin,
    }


def _funnel(
    inputs: Mapping[str, Any], counts: Mapping[str, int], cycle_days: Sequence[float]
) -> dict[str, Any]:
    section = inputs.get("funnel")
    source: Mapping[str, Any] = section if isinstance(section, dict) else {}
    win = v.win_rate(counts.get("won", 0), counts.get("lost", 0))
    deal, quota, pipeline = (
        _num(source, "avg_deal_size"),
        _num(source, "quota"),
        _num(source, "pipeline_value"),
    )
    cycle = v.cycle_time_percentiles(list(cycle_days))["p50"]
    velocity = None
    if win is not None and cycle and deal is not None:
        velocity = v.pipeline_velocity(counts.get("entered", 0), deal, win, cycle)
    return {
        "conversion_rate": v.conversion_rate(counts.get("converted", 0), counts.get("entered", 0)),
        "win_rate": win,
        "cycle_time_days": cycle,
        "velocity": velocity,
        "coverage": v.pipeline_coverage(pipeline, quota)
        if pipeline is not None and quota is not None
        else None,
    }


def _growth(inputs: Mapping[str, Any]) -> dict[str, Any]:
    source = inputs.get("growth")
    if not isinstance(source, dict):
        return {}
    names = ("starting_mrr", "expansion", "contraction", "churned", "new")
    start, exp, con, churn, new = (_num(source, n) for n in names)
    nrr = grr = None
    if start is not None:
        nrr = v.nrr(start, exp or 0.0, con or 0.0, churn or 0.0)
        grr = v.grr(start, con or 0.0, churn or 0.0)
    growth, margin = _num(source, "growth_pct"), _num(source, "profit_margin_pct")
    burn, arr = _num(source, "net_burn"), _num(source, "net_new_arr")
    delta, spend = _num(source, "delta_arr"), _num(source, "prior_quarter_sm")
    return {
        "mrr": None
        if start is None
        else start + v.net_new_mrr(new or 0.0, exp or 0.0, con or 0.0, churn or 0.0),
        "nrr": nrr,
        "grr": grr,
        "rule_of_40": v.rule_of_40(growth, margin)
        if growth is not None and margin is not None
        else None,
        "burn_multiple": v.burn_multiple(burn, arr)
        if burn is not None and arr is not None
        else None,
        "magic_number": v.magic_number(delta, spend)
        if delta is not None and spend is not None
        else None,
    }


def _portfolio(inputs: Mapping[str, Any]) -> dict[str, Any]:
    source = inputs.get("portfolio")
    if not isinstance(source, dict):
        return {}
    revenue = source.get("revenue_by_horizon")
    clean = (
        {str(k): float(x) for k, x in revenue.items() if isinstance(x, int | float)}
        if isinstance(revenue, dict)
        else {}
    )
    npvs = _series(source, "npvs")
    return {
        "horizon_mix": v.horizon_mix(clean) if clean else None,
        "hhi": v.hhi(list(clean.values())) if clean else None,
        "sharpe_like": v.sharpe_like(npvs) if npvs else None,
    }


def _meta_ml(
    scores: Sequence[tuple[float, bool]], holdout: Mapping[bool, Mapping[str, int]]
) -> dict[str, Any]:
    """V6 from core only. PSI, model utility and decision latency stay null (see reads)."""
    closed = [1 if won else 0 for _, won in scores]
    k = min(TOP_K, len(closed))
    lift = None
    treated, base = holdout.get(False), holdout.get(True)
    if treated and base:
        after = v.win_rate(treated["won"], treated["lost"])
        before = v.win_rate(base["won"], base["lost"])
        if after is not None and before is not None:
            lift = v.funnel_lift(after, before)
    return {
        "model_utility": None,
        "decision_latency_days": None,
        "psi": None,
        "precision_at_k": v.precision_at_k_real(closed, [s for s, _ in scores], k) if k else None,
        "funnel_lift": lift,
    }


def compute_project(
    project: Mapping[str, Any],
    counts: Mapping[str, int],
    cycle_days: Sequence[float],
) -> dict[str, dict[str, Any]]:
    """Return ``{table: values}`` for the five project cards; empty dicts are dropped later."""
    raw = project.get("inputs")
    inputs = json.loads(raw) if isinstance(raw, str) else (raw or {})
    if not isinstance(inputs, dict):
        inputs = {}
    return {
        "financial_run": _financial(inputs),
        "unit_economics": _unit_economics(inputs),
        "funnel_metric": _funnel(inputs, counts, cycle_days),
        "growth_metric": _growth(inputs),
        "portfolio_snapshot": _portfolio(inputs),
    }


def _row(
    table: str,
    values: Mapping[str, Any],
    tenant_id: UUID,
    project_id: UUID,
    now: datetime,
) -> dict[str, Any] | None:
    """Insert parameters, or None when the card has no value at all."""
    row: dict[str, Any] = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "source_version": PRODUCER_VERSION,
        "measured_at": now,
    }
    if table == "portfolio_snapshot":
        mix = values.get("horizon_mix")
        row["horizon_mix"] = json.dumps(mix or {})
        has_value = bool(mix)
        columns: tuple[str, ...] = ("hhi", "sharpe_like")
    else:
        columns = tuple(_FIT[table])
        has_value = False
    if table == "financial_run":
        row["assumptions"] = json.dumps(values.get("assumptions") or {})
    for column in columns:
        row[column] = fit(table, column, values.get(column))
        has_value = has_value or row[column] is not None
    if table == "financial_run":
        # discount_rate is an input, not a result; only results decide whether to write.
        has_value = any(row[c] is not None for c in columns if c != "discount_rate")
    return row if has_value else None


# --------------------------------------------------------------------- run loop


async def run_tenant(
    open_store: StoreFactory,
    tenant_id: UUID,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, int]:
    """Compute and store the metrics of one tenant in one transaction."""
    moment = now or datetime.now(UTC)
    written = skipped = 0
    async with open_store(tenant_id) as store:
        projects = await store.projects(tenant_id, limit)
        for project in projects:
            line = project.get("service_line")
            counts = await store.lead_counts(tenant_id, line)
            cycle = await store.won_cycle_days(tenant_id, line)
            for table, values in compute_project(project, counts, cycle).items():
                row = _row(table, values, tenant_id, project["id"], moment)
                if row is None:
                    skipped += 1
                    continue
                await store.insert(table, row)
                written += 1
        meta = _meta_ml(await store.closed_scores(tenant_id), await store.holdout_counts(tenant_id))
        row = _row("meta_ml_metric", meta, tenant_id, TENANT_WIDE_PROJECT, moment)
        if row is None:
            skipped += 1
        else:
            await store.insert("meta_ml_metric", row)
            written += 1
    return {"projects": len(projects), "written": written, "skipped": skipped}


async def run_passes(
    open_store: StoreFactory,
    tenants: Sequence[UUID],
    *,
    passes: int = 1,
    interval_seconds: float = 0.0,
    limit: int = DEFAULT_LIMIT,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[dict[str, Any]]:
    """Run ``passes`` passes over ``tenants`` (the stop condition) and return one result each."""
    results: list[dict[str, Any]] = []
    for index in range(max(passes, 0)):
        if index:
            await sleep(interval_seconds)
        for tenant_id in tenants:
            try:
                outcome = await run_tenant(open_store, tenant_id, limit=limit)
            except Exception:
                _log.exception("validation worker failed for tenant %s", tenant_id)
                results.append({"tenant_id": str(tenant_id), "error": True})
            else:
                results.append({"tenant_id": str(tenant_id), **outcome})
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validation_worker", description=__doc__)
    parser.add_argument("--tenant", action="append", required=True, type=UUID)
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--interval-seconds", type=float, default=3600.0)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    return parser


async def run(argv: Sequence[str] | None = None) -> int:
    from libs.common.settings import get_settings
    from services.core.app.repository import CoreRepository

    args = build_parser().parse_args(argv)
    repository = CoreRepository(get_settings().database_url)
    try:
        results = await run_passes(
            sql_store_factory(repository.engine),
            args.tenant,
            passes=args.passes,
            interval_seconds=args.interval_seconds,
            limit=args.limit,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await repository.close()
    print(json.dumps(results))
    return 1 if any(r.get("error") for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
