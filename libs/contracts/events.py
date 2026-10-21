"""Define the versioned event envelope that travels through ``invent.events``.

``DomainEvent`` fixes the common envelope and ``EventType`` limits the names
services can publish. It relates to the outbox in
``services/core/app/repository.py``, the pulse and ML consumers, and the
``core.outbox_event`` table from the migration. Its purpose is to support
at-least-once delivery with idempotent consumers while introducing the v2.2
tenant and replay metadata additively.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    SIGNAL_CREATED = "signal.created"
    COMPANY_UPDATED = "company.updated"
    LEAD_CREATED = "lead.created"
    LEAD_OUTCOME_RECORDED = "lead.outcome_recorded"
    LEAD_SCORE_UPDATED = "lead.score_updated"
    SIGNAL_ACTIVITY_LOGGED = "signal.activity_logged"
    SIGNAL_CONVERTED_TO_PROJECT = "signal.converted_to_project"
    TENANT_CONFIG_UPDATED = "tenant.config_updated"
    INGEST_QUARANTINE = "ingest.quarantine"


class ReservedEventType(StrEnum):
    """Reserved L2 and L3 event names; they have no handlers in L1 (REQ-EXT-001)."""

    SIGNAL_TAGGED = "signal.tagged"
    SIGNAL_SCORED = "signal.scored"
    COMPANY_MATCHED = "company.matched"
    SIGNAL_EXPLAINED = "signal.explained"
    FUNNEL_GATE_DECISION = "funnel.gate_decision"
    FUNNEL_PIVOT_REQUESTED = "funnel.pivot_requested"
    FUNNEL_PROJECT_KILLED = "funnel.project_killed"
    FUNNEL_KILL_TRIGGER_EVENT = "funnel.kill_trigger_event"
    PROJECT_PIPELINE_UPDATED = "project.pipeline_updated"
    PROJECT_FORECAST_UPDATED = "project.forecast_updated"
    REVIEW_CANDIDATE = "review.candidate"
    COMPONENT_EVALUATED = "component.evaluated"


class DomainEvent(BaseModel):
    """Describe a replayable event with compatibility defaults for F0 producers."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    aggregate_type: str
    aggregate_id: UUID
    version: int = 1
    source_version: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    occurred_at: datetime
    payload: dict[str, Any]
    request_id: str | None = None
    schema_version: int = Field(default=1, ge=1)
    tenant_id: UUID | None = None
    replay: bool = False

    @property
    def occurred_at_ms(self) -> int:
        """Return the event instant in the v2.2 millisecond UTC representation."""

        return int(self.occurred_at.timestamp() * 1000)
