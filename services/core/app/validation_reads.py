"""Read the latest validation measurement of a card, with the threshold decision (T-147).

``main.py`` serves ``/internal/core/validation/``: ``projects/{project_id}/{card}`` for the six
cards of ``services/streamlit/pages/9_Validation.py`` and ``meta-ml`` for page 7. Every read runs
in one transaction that calls ``set_tenant_context`` first (REQ-SEC-005). Only ``core`` and
``validation`` tables are read (INV-01). The response is the newest row of the card, the status
of every field that has a rule in ``libs/domain/validation_thresholds.yaml`` (T-108) and the
fields this service can never fill, each with a reason. ``validation_worker.py`` writes the rows.
O(1) per read.
"""

import json
import sys
from collections.abc import Mapping
from decimal import Decimal
from types import TracebackType
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from libs.common.tenant import set_tenant_context
from libs.domain.validation import evaluate, horizon_mix_deviation, load_thresholds

# Project id of the tenant wide V6 row (validation.meta_ml_metric.project_id is NOT NULL).
TENANT_WIDE_PROJECT = UUID(int=0)

# URL segment -> (table, value fields in the order of the page).
CARD_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "financial": (
        "financial_run",
        ("npv", "irr", "payback_periods", "discounted_payback_periods", "emv"),
    ),
    "unit-economics": (
        "unit_economics",
        ("cac", "ltv", "ltv_cac_ratio", "cac_payback_months", "gross_margin"),
    ),
    "funnel": (
        "funnel_metric",
        ("conversion_rate", "win_rate", "cycle_time_days", "velocity", "coverage"),
    ),
    "growth": (
        "growth_metric",
        ("mrr", "nrr", "grr", "rule_of_40", "burn_multiple", "magic_number"),
    ),
    "portfolio": ("portfolio_snapshot", ("horizon_mix", "hhi", "sharpe_like")),
    "meta-ml": (
        "meta_ml_metric",
        ("model_utility", "decision_latency_days", "psi", "precision_at_k", "funnel_lift"),
    ),
}

# Field -> threshold metric of validation_thresholds.yaml (same mapping as the page).
THRESHOLD_KEYS: dict[str, str] = {
    "irr": "irr",
    "payback_periods": "payback_months",
    "ltv_cac_ratio": "ltv_cac",
    "cac_payback_months": "cac_payback_months",
    "coverage": "pipeline_coverage",
    "burn_multiple": "burn_multiple",
    "nrr": "nrr",
    "grr": "grr",
    "rule_of_40": "rule_of_40",
    "magic_number": "magic_number",
    "psi": "psi",
    "horizon_mix_deviation": "horizon_mix_deviation",
}

# Fields core can never fill, with the reason (INV-01 or missing source events).
UNAVAILABLE: dict[str, dict[str, str]] = {
    "meta-ml": {
        "psi": "needs_ml_schema_inv01",
        "model_utility": "needs_value_baseline_inputs",
        "decision_latency_days": "needs_presented_and_confirmed_events",
    },
}


# V1, V2, V4 and V5 have no worker input until a strategist writes it (D-24, T-179).
INPUT_CARDS = frozenset({"financial", "unit-economics", "growth", "portfolio"})
NEEDS_INPUT = "needs_strategist_input"

_PROJECT_EXISTS = """
    SELECT id FROM core.project WHERE id = :id AND tenant_id = :tenant_id
"""


class ValidationReadTransaction:
    """Reads bound to one open connection whose tenant context is already set."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def project_exists(self, project_id: UUID, tenant_id: UUID) -> bool:
        result = await self.connection.execute(
            text(_PROJECT_EXISTS), {"id": project_id, "tenant_id": tenant_id}
        )
        return result.first() is not None

    async def latest(self, card: str, project_id: UUID, tenant_id: UUID) -> dict[str, Any] | None:
        table = CARD_FIELDS[card][0]
        sql: str
        if table == "financial_run":
            sql = """
        SELECT project_id, source_version, measured_at, npv, irr, payback_periods,
               discounted_payback_periods, emv, assumptions->>'horizon' AS horizon
        FROM validation.financial_run
        WHERE tenant_id = :tenant_id AND project_id = :project_id
        ORDER BY measured_at DESC, created_at DESC LIMIT 1
    """
        elif table == "unit_economics":
            sql = """
        SELECT project_id, source_version, measured_at, cac, ltv, ltv_cac_ratio,
               cac_payback_months, gross_margin
        FROM validation.unit_economics
        WHERE tenant_id = :tenant_id AND project_id = :project_id
        ORDER BY measured_at DESC, created_at DESC LIMIT 1
    """
        elif table == "funnel_metric":
            sql = """
        SELECT project_id, source_version, measured_at, conversion_rate, win_rate,
               cycle_time_days, velocity, coverage
        FROM validation.funnel_metric
        WHERE tenant_id = :tenant_id AND project_id = :project_id
        ORDER BY measured_at DESC, created_at DESC LIMIT 1
    """
        elif table == "growth_metric":
            sql = """
        SELECT project_id, source_version, measured_at, mrr, nrr, grr, rule_of_40,
               burn_multiple, magic_number
        FROM validation.growth_metric
        WHERE tenant_id = :tenant_id AND project_id = :project_id
        ORDER BY measured_at DESC, created_at DESC LIMIT 1
    """
        elif table == "portfolio_snapshot":
            sql = """
        SELECT project_id, source_version, measured_at, horizon_mix, hhi, sharpe_like
        FROM validation.portfolio_snapshot
        WHERE tenant_id = :tenant_id AND project_id = :project_id
        ORDER BY measured_at DESC, created_at DESC LIMIT 1
    """
        elif table == "meta_ml_metric":
            sql = """
        SELECT project_id, source_version, measured_at, model_utility,
               decision_latency_days, psi, precision_at_k, funnel_lift
        FROM validation.meta_ml_metric
        WHERE tenant_id = :tenant_id AND project_id = :project_id
        ORDER BY measured_at DESC, created_at DESC LIMIT 1
    """
        else:
            raise KeyError(table)
        result = await self.connection.execute(
            text(sql), {"tenant_id": tenant_id, "project_id": project_id}
        )
        row = result.mappings().first()
        return dict(row) if row else None


class ValidationReadUnitOfWork:
    """One transaction per read; ``set_tenant_context`` runs before the first statement."""

    def __init__(self, engine: AsyncEngine, tenant_id: UUID) -> None:
        self._context = engine.begin()
        self._tenant_id = tenant_id

    async def __aenter__(self) -> ValidationReadTransaction:
        connection = await self._context.__aenter__()
        try:
            await set_tenant_context(connection, self._tenant_id)
        except BaseException:
            await self._context.__aexit__(*sys.exc_info())
            raise
        return ValidationReadTransaction(connection)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._context.__aexit__(exc_type, exc, tb)


def validation_reader(engine: AsyncEngine | None, tenant_id: UUID) -> ValidationReadUnitOfWork:
    if engine is None:
        raise RuntimeError("DATABASE_URL is required for core reads")
    return ValidationReadUnitOfWork(engine, tenant_id)


class ProjectNotFoundError(Exception):
    """The project does not exist for the tenant."""


class CardNotFoundError(Exception):
    """The card name is not one of ``CARD_FIELDS``."""


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def decide(
    card: str,
    row: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """Status of every field of the row that has a rule (``unknown`` when the value is null)."""
    config = thresholds if thresholds is not None else load_thresholds()
    variant = row.get("horizon") if isinstance(row.get("horizon"), str) else None
    out: dict[str, dict[str, str]] = {}
    for name, metric in THRESHOLD_KEYS.items():
        if name not in row:
            continue
        out[name] = {
            "metric": metric,
            "status": evaluate(metric, _number(row[name]), variant=variant, thresholds=config),
        }
    return out


def build_card(card: str, project_id: UUID, row: Mapping[str, Any] | None) -> dict[str, Any]:
    """Shape one card response; an empty card carries ``reason`` instead of values."""
    fields = CARD_FIELDS[card][1]
    config = load_thresholds()
    body: dict[str, Any] = {
        "card": card,
        "project_id": str(project_id),
        "threshold_version": config.get("version"),
        "unavailable": dict(UNAVAILABLE.get(card, {})),
    }
    if row is None:
        body |= {
            "empty": True,
            "reason": NEEDS_INPUT if card in INPUT_CARDS else "no_measurement",
            "measured_at": None,
            "source_version": None,
            "thresholds": {},
        } | dict.fromkeys(fields)
        return body
    row = {**row}
    if isinstance(row.get("horizon_mix"), str):
        row["horizon_mix"] = json.loads(row["horizon_mix"])
    values = {name: _plain(row.get(name)) for name in fields}
    flat = dict(values)
    if "horizon" in row:
        flat["horizon"] = row["horizon"]
    if card == "portfolio":
        mix = row.get("horizon_mix")
        deviation = horizon_mix_deviation(mix) if isinstance(mix, dict) and mix else None
        flat["horizon_mix_deviation"] = deviation
    body |= flat | {
        "empty": all(values[name] is None for name in fields),
        "reason": None,
        "measured_at": _plain(row.get("measured_at")),
        "source_version": row.get("source_version"),
        "thresholds": decide(card, flat, config),
    }
    if body["empty"]:
        body["reason"] = "no_computable_value"
    return body


async def read_card(
    unit_of_work: ValidationReadUnitOfWork,
    tenant_id: UUID,
    card: str,
    project_id: UUID | None,
) -> dict[str, Any]:
    """Latest row of a card. ``project_id=None`` is the tenant wide V6 read of page 7.

    The per-project ``meta-ml`` card serves the same tenant wide V6 row, because V6 measures the
    platform; the project must still exist for the tenant.
    """
    if card not in CARD_FIELDS:
        raise CardNotFoundError(card)
    key = TENANT_WIDE_PROJECT if card == "meta-ml" or project_id is None else project_id
    async with unit_of_work as tx:
        if project_id is not None and not await tx.project_exists(project_id, tenant_id):
            raise ProjectNotFoundError(str(project_id))
        row = await tx.latest(card, key, tenant_id)
    return build_card(card, project_id or TENANT_WIDE_PROJECT, row)
