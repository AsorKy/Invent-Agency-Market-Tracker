"""Define Pydantic input and output models for internal APIs.

The models validate signals, Bright Data webhooks, and scoring requests before
they reach repositories. They relate to ``services/ingest/app/main.py``,
``services/core/app/main.py``, and ``services/ml/app/main.py``; their fields
must evolve with the workflows in ``orchestration/n8n``. They provide an
executable contract so each service interprets JSON consistently.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SignalCreate(BaseModel):
    company_id: UUID
    source: str
    signal_type: str
    observed_at: datetime
    strength: float = Field(default=0.5, ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    source_external_id: str | None = None


class BrightDataWebhook(BaseModel):
    snapshot_id: str
    dataset_id: str
    status: str = "ready"
    payload: dict[str, Any] | list[Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ScoreFeatures(BaseModel):
    company_id: UUID
    icp_fit: float = Field(ge=0, le=1)
    signal_strength: float = Field(ge=0, le=1)
    territory_opportunity: float = Field(ge=0, le=1)
    source_version: str = "draft"


class ScoreResponse(BaseModel):
    company_id: UUID
    score: int
    status: str
    stale: bool = False
    score_version: str
