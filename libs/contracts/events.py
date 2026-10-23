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

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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


class ChainPayload(BaseModel):
    """Base of the typed chain payloads (REQ-EVT-008, D-20).

    ``parent_event_id`` is a mandatory key (a UUID or null). ``source_version`` and ``parent``
    are the optional stamps that ``libs/domain/event_chain.py`` writes today; they stay
    declared so ``extra="forbid"`` rejects every other key.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = Field(default=1, ge=1)
    parent_event_id: UUID | None
    source_version: str | None = None
    parent: dict[str, str] | None = None


class SignalTaggedPayload(ChainPayload):
    signal_id: UUID
    company_id: UUID
    tags: dict[str, str]


class SignalScoredPayload(ChainPayload):
    # signal_id and score_version are not produced yet (T-175, T-176); optional until then.
    signal_id: UUID | None = None
    company_id: UUID
    score: int
    score_version: str | None = None


class CompanyMatchedPayload(ChainPayload):
    # rues_matched is not produced yet; optional until then.
    company_id: UUID
    lead_id: UUID
    rues_matched: bool | None = None


class SignalExplainedPayload(ChainPayload):
    # Producers write the text under ``reason``; both names are accepted on input.
    lead_id: UUID
    company_id: UUID
    qualification_reason: dict[str, Any] | None = Field(
        default=None, validation_alias=AliasChoices("qualification_reason", "reason")
    )


class ProjectPipelineUpdatedPayload(ChainPayload):
    lead_id: UUID
    project_id: UUID | None = None
    from_stage: str = Field(alias="from")
    to_stage: str = Field(alias="to")
    version: int


class ProjectForecastUpdatedPayload(ChainPayload):
    lead_id: UUID
    project_id: UUID | None = None
    stage: str


CHAIN_PAYLOAD_MODELS: dict[str, type[ChainPayload]] = {
    ReservedEventType.SIGNAL_TAGGED.value: SignalTaggedPayload,
    ReservedEventType.SIGNAL_SCORED.value: SignalScoredPayload,
    ReservedEventType.COMPANY_MATCHED.value: CompanyMatchedPayload,
    ReservedEventType.SIGNAL_EXPLAINED.value: SignalExplainedPayload,
    ReservedEventType.PROJECT_PIPELINE_UPDATED.value: ProjectPipelineUpdatedPayload,
    ReservedEventType.PROJECT_FORECAST_UPDATED.value: ProjectForecastUpdatedPayload,
}


def validate_chain_payload(event_type: str, payload: dict[str, Any]) -> ChainPayload:
    """Validate ``payload`` against the typed model of ``event_type``.

    Payloads written before T-175 lack the ``parent_event_id`` key; it is then derived from
    ``payload["parent"]["event_id"]`` (null when there is no parent block). Raises ``KeyError``
    for a type without a model and ``pydantic.ValidationError`` for an invalid payload.
    """
    model = CHAIN_PAYLOAD_MODELS[event_type]
    data = dict(payload)
    if "parent_event_id" not in data:
        parent = data.get("parent")
        data["parent_event_id"] = parent.get("event_id") if isinstance(parent, dict) else None
    return model.model_validate(data)
