"""Record lead outcomes and activities and convert a lead into a project.

Each function runs in one ``ActionUnitOfWork`` transaction: the row (``core.lead_outcome``,
``core.lead_activity`` or ``core.project``), the audit trail where relevant, and the outbox event
(``lead.outcome_recorded``, ``signal.activity_logged``, ``signal.converted_to_project``) commit or
roll back together. It relates to ``leads.py`` (shared lead lookup), ``repository.py`` (SQL) and
``main.py`` (routes). Outcomes are the supervised-learning labels of principle P7 (REQ-COR-003,
REQ-COR-005). Each call is O(1) in queries.
"""

from datetime import UTC, datetime
from enum import StrEnum
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from libs.contracts.events import EventType
from services.core.app.leads import LeadNotFoundError


class OutcomeType(StrEnum):
    CONTACT = "contact"
    RESPONSE = "response"
    MEETING = "meeting"
    CLOSE = "close"
    SPAM = "spam"


class CloseResult(StrEnum):
    WON = "won"
    LOST = "lost"


class ActivityType(StrEnum):
    CALL = "call"
    EMAIL = "email"
    DEMO = "demo"
    NOTE = "note"


class OutcomeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: OutcomeType
    result: CloseResult | None = None
    notes: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def _close_needs_result(self) -> "OutcomeCreate":
        if self.outcome is OutcomeType.CLOSE and self.result is None:
            raise ValueError("close outcome requires result")
        if self.outcome is not OutcomeType.CLOSE and self.result is not None:
            raise ValueError("result is only valid for close outcomes")
        return self


class ActivityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_type: ActivityType
    channel: str | None = Field(default=None, max_length=60)
    source: str | None = Field(default=None, max_length=60)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None
    created_by: UUID | None = None


class ProjectCreate(BaseModel):
    """Conversion body: creates the project for ``lead_id``."""

    model_config = ConfigDict(extra="forbid")

    lead_id: UUID
    name: str = Field(min_length=1, max_length=300)
    hypothesis: str = Field(min_length=1, max_length=4000)
    service_line: str | None = Field(default=None, max_length=120)
    target_market: str | None = Field(default=None, max_length=300)
    estimated_roi: float | None = None
    timeline: str | None = Field(default=None, max_length=300)


class LeadAlreadyConvertedError(Exception):
    def __init__(self, project_id: UUID) -> None:
        super().__init__("lead_already_converted")
        self.project_id = project_id


class LeadClosedError(Exception):
    """A lost lead cannot be converted."""


class ActionTransaction(Protocol):
    async def get_lead(self, lead_id: UUID, tenant_id: UUID | None) -> dict[str, Any] | None: ...
    async def insert_outcome(self, row: dict[str, Any]) -> None: ...
    async def find_activity(self, tenant_id: UUID, key: str) -> dict[str, Any] | None: ...
    async def insert_activity(self, row: dict[str, Any]) -> None: ...
    async def find_project_for_lead(self, lead_id: UUID) -> UUID | None: ...
    async def insert_project(self, row: dict[str, Any]) -> None: ...
    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None: ...


class ActionUnitOfWork(Protocol):
    async def __aenter__(self) -> ActionTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


def outcome_label(payload: OutcomeCreate) -> str:
    """Stored outcome name: ``close_won`` and ``close_lost`` keep the label for ML."""
    if payload.result is not None:
        return f"{payload.outcome.value}_{payload.result.value}"
    return payload.outcome.value


async def record_outcome(
    unit_of_work: ActionUnitOfWork,
    lead_id: UUID,
    payload: OutcomeCreate,
    *,
    tenant_id: UUID | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    async with unit_of_work as tx:
        lead = await tx.get_lead(lead_id, tenant_id)
        if lead is None:
            raise LeadNotFoundError(str(lead_id))
        row: dict[str, Any] = {
            "id": uuid4(),
            "tenant_id": lead.get("tenant_id"),
            "lead_id": lead_id,
            "outcome": outcome_label(payload),
            "notes": payload.notes,
            "occurred_at": payload.occurred_at or moment,
        }
        await tx.insert_outcome(row)
        await tx.add_outbox_event(
            event_id=uuid4(),
            tenant_id=row["tenant_id"],
            aggregate_type="lead",
            aggregate_id=lead_id,
            event_type=EventType.LEAD_OUTCOME_RECORDED.value,
            payload={
                "outcome_id": str(row["id"]),
                "lead_id": str(lead_id),
                "company_id": str(lead["company_id"]),
                "service_line": lead["service_line"],
                "outcome": row["outcome"],
                "occurred_at": row["occurred_at"].isoformat(),
                "request_id": request_id,
            },
        )
        return {"outcome_id": str(row["id"]), "outcome": row["outcome"]}


async def log_activity(
    unit_of_work: ActionUnitOfWork,
    lead_id: UUID,
    payload: ActivityCreate,
    *,
    tenant_id: UUID,
    idempotency_key: str | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return ``(response, created)``; a repeated idempotency key returns the stored row."""
    moment = now or datetime.now(UTC)
    async with unit_of_work as tx:
        if idempotency_key:
            existing = await tx.find_activity(tenant_id, idempotency_key)
            if existing is not None:
                return {"activity_id": str(existing["id"])}, False
        lead = await tx.get_lead(lead_id, tenant_id)
        if lead is None:
            raise LeadNotFoundError(str(lead_id))
        row: dict[str, Any] = {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "activity_type": payload.activity_type.value,
            "channel": payload.channel,
            "source": payload.source,
            "payload": payload.payload,
            "occurred_at": payload.occurred_at or moment,
            "created_by": payload.created_by,
            "idempotency_key": idempotency_key,
        }
        await tx.insert_activity(row)
        await tx.add_outbox_event(
            event_id=uuid4(),
            tenant_id=row["tenant_id"],
            aggregate_type="lead",
            aggregate_id=lead_id,
            event_type=EventType.SIGNAL_ACTIVITY_LOGGED.value,
            payload={
                "activity_id": str(row["id"]),
                "lead_id": str(lead_id),
                "activity_type": row["activity_type"],
                "channel": payload.channel,
                "occurred_at": row["occurred_at"].isoformat(),
                "request_id": request_id,
            },
        )
        return {"activity_id": str(row["id"])}, True


async def convert_to_project(
    unit_of_work: ActionUnitOfWork,
    payload: ProjectCreate,
    *,
    tenant_id: UUID | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create the project and its event in one transaction; one project per lead."""
    moment = now or datetime.now(UTC)
    async with unit_of_work as tx:
        lead = await tx.get_lead(payload.lead_id, tenant_id)
        if lead is None:
            raise LeadNotFoundError(str(payload.lead_id))
        if lead["stage"] == "lost":
            raise LeadClosedError(str(payload.lead_id))
        existing = await tx.find_project_for_lead(payload.lead_id)
        if existing is not None:
            raise LeadAlreadyConvertedError(existing)
        row: dict[str, Any] = {
            "id": uuid4(),
            "tenant_id": lead.get("tenant_id"),
            "lead_id": payload.lead_id,
            "name": payload.name,
            "service_line": payload.service_line or lead["service_line"],
            "hypothesis": payload.hypothesis,
            "status": "draft",
            "estimated_roi": payload.estimated_roi,
            "metadata": {"target_market": payload.target_market, "timeline": payload.timeline},
            "created_at": moment,
            "updated_at": moment,
        }
        await tx.insert_project(row)
        await tx.add_outbox_event(
            event_id=uuid4(),
            tenant_id=row["tenant_id"],
            aggregate_type="project",
            aggregate_id=row["id"],
            event_type=EventType.SIGNAL_CONVERTED_TO_PROJECT.value,
            payload={
                "project_id": str(row["id"]),
                "lead_id": str(payload.lead_id),
                "company_id": str(lead["company_id"]),
                "service_line": row["service_line"],
                "request_id": request_id,
            },
        )
        return {"project_id": str(row["id"]), "lead_id": str(payload.lead_id), "status": "draft"}
