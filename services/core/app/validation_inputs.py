"""Validate and store the strategist inputs of validation cards V1, V2, V4 and V5 (T-179).

``PUT /internal/core/projects/{id}/validation-inputs`` in ``main.py`` calls ``update_inputs``.
``ValidationInputs`` is the typed form (``extra="forbid"``) of the keys that
``validation_worker.py`` reads from ``core.project.metadata->'validation'``: ``financial``,
``unit_economics``, ``growth`` and ``portfolio``. Only that key is written; the rest of
``metadata`` is untouched and an existing ``funnel`` section (V3 inputs, not part of this
object) is carried over. The version of the object lives at ``validation.version`` and drives
the ETag and ``If-Match`` exactly as in ``tenant_config.py`` (0 when no input exists, a stale
value answers 412). One transaction holds the tenant context, the project row lock, the write
and the ``core.audit_log`` row (REQ-LNS-014, REQ-SEC-005, D-24). O(1) in queries.
"""

import json
from datetime import UTC, datetime
from types import TracebackType
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AUDIT_ACTION = "validation.inputs_updated"
# Sections kept from the stored object although this model does not own them (V3 inputs).
CARRIED_SECTIONS = ("funnel",)

Money = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False)]
Signed = Annotated[float, Field(ge=-1e12, le=1e12, allow_inf_nan=False)]
Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Percent = Annotated[float, Field(ge=-1000, le=1000, allow_inf_nan=False)]
Horizon = Literal["H1", "H2", "H3"]


class Financial(BaseModel):
    """V1: cash flows per period (first one usually negative) and the discount rate."""

    model_config = ConfigDict(extra="forbid")

    discount_rate: Fraction
    cash_flows: Annotated[list[Signed], Field(min_length=1, max_length=240)]
    horizon: Horizon | None = None
    outcomes: list[tuple[Fraction, Signed]] | None = Field(default=None, max_length=100)


class UnitEconomics(BaseModel):
    """V2: acquisition cost, customers, revenue per account, revenue and cost of goods."""

    model_config = ConfigDict(extra="forbid")

    acquisition_cost: Money | None = None
    new_customers: Money | None = None
    arpa: Money | None = None
    revenue: Money | None = None
    cogs: Money | None = None
    monthly_churn: Fraction | None = None


class Growth(BaseModel):
    """V4: monthly recurring revenue movements and the SaaS efficiency inputs."""

    model_config = ConfigDict(extra="forbid")

    starting_mrr: Money | None = None
    expansion: Money | None = None
    contraction: Money | None = None
    churned: Money | None = None
    new: Money | None = None
    growth_pct: Percent | None = None
    profit_margin_pct: Percent | None = None
    net_burn: Signed | None = None
    net_new_arr: Signed | None = None
    delta_arr: Signed | None = None
    prior_quarter_sm: Money | None = None


class Portfolio(BaseModel):
    """V5: revenue per horizon and the NPV of each project of the portfolio."""

    model_config = ConfigDict(extra="forbid")

    revenue_by_horizon: dict[Horizon, Money] | None = None
    npvs: Annotated[list[Signed], Field(max_length=1000)] | None = None


class ValidationInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    financial: Financial | None = None
    unit_economics: UnitEconomics | None = None
    growth: Growth | None = None
    portfolio: Portfolio | None = None


class StaleInputsError(Exception):
    """``If-Match`` does not match the stored version (HTTP 412)."""

    def __init__(self, current_version: int) -> None:
        super().__init__("stale_etag")
        self.current_version = current_version


class ProjectNotFoundError(Exception):
    """The project does not exist for the tenant."""


class InputsTransaction(Protocol):
    async def get_inputs(self, project_id: UUID, tenant_id: UUID) -> dict[str, Any] | None: ...
    async def set_inputs(
        self, project_id: UUID, tenant_id: UUID, inputs: dict[str, Any]
    ) -> None: ...
    async def add_audit(
        self,
        *,
        action: str,
        project_id: UUID,
        tenant_id: UUID | None,
        payload: dict[str, object],
    ) -> None: ...


class InputsUnitOfWork(Protocol):
    async def __aenter__(self) -> InputsTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


def _stored(raw: Any) -> dict[str, Any]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else {}


def _version(stored: dict[str, Any]) -> int:
    value = stored.get("version")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _view(project_id: UUID, version: int, stored: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": str(project_id),
        "version": version,
        "validation": {k: v for k, v in stored.items() if k != "version"},
    }


async def update_inputs(
    unit_of_work: InputsUnitOfWork,
    tenant_id: UUID,
    project_id: UUID,
    inputs: ValidationInputs,
    *,
    if_match: int,
    updated_by: UUID | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replace the inputs when ``if_match`` equals the stored version (0 when absent)."""
    moment = now or datetime.now(UTC)
    body = inputs.model_dump(mode="json", exclude_none=True)
    async with unit_of_work as tx:
        old = await tx.get_inputs(project_id, tenant_id)
        if old is None:
            raise ProjectNotFoundError(str(project_id))
        stored = _stored(old.get("inputs"))
        current = _version(stored)
        if if_match != current:
            raise StaleInputsError(current)
        carried = {k: stored[k] for k in CARRIED_SECTIONS if k in stored}
        if current and {k: v for k, v in stored.items() if k != "version"} == body | carried:
            return _view(project_id, current, stored)
        version = current + 1
        new = body | carried | {"version": version}
        await tx.set_inputs(project_id, tenant_id, new)
        await tx.add_audit(
            action=AUDIT_ACTION,
            project_id=project_id,
            tenant_id=tenant_id,
            payload={
                "project_id": str(project_id),
                "version": version,
                "previous_version": current,
                "sections": sorted(body),
                "updated_by": str(updated_by) if updated_by else None,
                "updated_at": moment.isoformat(),
                "request_id": request_id,
            },
        )
        return _view(project_id, version, new)
