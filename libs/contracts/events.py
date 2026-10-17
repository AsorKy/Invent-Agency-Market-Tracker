"""Define event types that travel through ``invent.events``.

``DomainEvent`` fixes the common envelope and ``EventType`` limits the names
services can publish. It relates to the outbox in
``services/core/app/repository.py``, the analytics and ML consumers, and the
``core.outbox_event`` table from the migration. Its purpose is to support
at-least-once delivery with idempotent consumers.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EventType(StrEnum):
    SIGNAL_CREATED = "signal.created"
    COMPANY_UPDATED = "company.updated"
    LEAD_CREATED = "lead.created"
    LEAD_OUTCOME_RECORDED = "lead.outcome_recorded"
    LEAD_SCORE_UPDATED = "lead.score_updated"
    INGEST_QUARANTINE = "ingest.quarantine"


class DomainEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    aggregate_type: str
    aggregate_id: UUID
    version: int = 1
    occurred_at: datetime
    payload: dict[str, Any]
    request_id: str | None = None
