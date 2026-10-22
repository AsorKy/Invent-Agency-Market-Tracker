"""Define Pydantic input and output models for public and internal APIs.

The models validate signals, Bright Data webhooks, and scoring requests before
they reach repositories. They relate to ``services/ingest/app/main.py``,
``services/core/app/main.py``, ``services/ml/app/main.py``,
``services/gateway/app/main.py``, and the future ``pulse`` and ``brief``
services. Their fields must evolve with the workflows in
``orchestration/n8n``. They provide an executable contract so each service
interprets JSON consistently while preserving the original F0 models during
the v2.2 migration.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

EpochMilliseconds = Annotated[int, Field(ge=0)]


class ContractModel(BaseModel):
    """Base model for additive v2.2 contracts with strict extra-field handling."""

    model_config = ConfigDict(extra="forbid")


class SignalCreate(BaseModel):
    company_id: UUID
    source: str
    signal_type: str
    observed_at: datetime
    strength: float = Field(default=0.5, ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    source_external_id: str | None = None
    tenant_id: UUID | None = None


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
    rules_source: Literal["default", "tenant"] = "default"


class PulsePoint(ContractModel):
    """Represent one time bucket in the unprocessed-signal rollup."""

    t: str
    total: int = Field(ge=0)
    failed: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0, le=1)


class UnprocessedSignalsResponse(ContractModel):
    """Return signal processing counts with an explicit freshness timestamp."""

    points: list[PulsePoint]
    as_of: EpochMilliseconds
    stale: bool
    method_version: str | None = None


class PipelineBacklogResponse(ContractModel):
    """Return active lead volume and the current team capacity estimate."""

    active: int | None = Field(default=None, ge=0)
    hot: int | None = Field(default=None, ge=0)
    capacity_estimate: int | None = Field(default=None, ge=0)
    as_of: EpochMilliseconds
    stale: bool
    method_version: str | None = None


class SourceEfficiency(ContractModel):
    """Return source efficiency using decimal strings for monetary values."""

    name: str
    cost_usd: str | None = None
    leads: int = Field(ge=0)
    meetings: int = Field(ge=0)
    cost_per_meeting: str | None = None
    roas: float | None = Field(default=None, ge=0)


class SourceEfficiencyResponse(ContractModel):
    """Return source efficiency rows and the timestamp of the rollup."""

    sources: list[SourceEfficiency]
    as_of: EpochMilliseconds
    stale: bool
    method_version: str | None = None


class PipelineTimeGroup(ContractModel):
    """Represent one p50/p90 lead-time group in Pulse."""

    key: str
    p50_days: float | None = Field(default=None, ge=0)
    p90_days: float | None = Field(default=None, ge=0)
    n: int = Field(ge=0)
    low_sample: bool = False


class TimeInPipelineResponse(ContractModel):
    """Return lead-time groups with low-sample annotations."""

    groups: list[PipelineTimeGroup]
    as_of: EpochMilliseconds
    stale: bool
    method_version: str | None = None


class TopSource(ContractModel):
    """Represent one ranked source of qualified leads."""

    source: str
    qualified_leads: int = Field(ge=0)
    conversion_rate: float | None = Field(default=None, ge=0, le=1)


class TopSourcesResponse(ContractModel):
    """Return ranked lead sources and their freshness timestamp."""

    ranking: list[TopSource]
    as_of: EpochMilliseconds
    stale: bool
    method_version: str | None = None


class BriefAlert(ContractModel):
    """Represent a severity-coded alert shown in a company brief."""

    code: str
    severity: str
    message: str


class BriefResponse(ContractModel):
    """Return a versioned company brief with stale and degraded indicators."""

    company_id: UUID
    source_version: int = Field(ge=0)
    content: dict[str, Any]
    generated_at: EpochMilliseconds
    summarizer_version: str
    stale: bool = False
    degraded: bool = False
    drivers: list[dict[str, Any]] | None = None
    counterfactual: dict[str, Any] | None = None


class ImportEntity(StrEnum):
    """Enumerate entities accepted by the background import API."""

    COMPANIES = "companies"
    LEADS = "leads"
    ACTIVITIES = "activities"


class ImportJobStatus(StrEnum):
    """Enumerate the lifecycle states returned by the jobs API."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ImportJobAccepted(ContractModel):
    """Return the asynchronous job handle created by an import request."""

    job_id: str
    status: ImportJobStatus = ImportJobStatus.QUEUED
    status_url: str


class ImportProgress(ContractModel):
    """Return bounded counters for an import job."""

    total: int = Field(ge=0)
    processed: int = Field(ge=0)
    upserted: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    errors: int = Field(ge=0)


class ImportJobResponse(ContractModel):
    """Return the current state and progress of an import job."""

    job_id: str
    status: ImportJobStatus
    progress: ImportProgress
    started_at: EpochMilliseconds | None = None
    finished_at: EpochMilliseconds | None = None
    error: str | None = None
