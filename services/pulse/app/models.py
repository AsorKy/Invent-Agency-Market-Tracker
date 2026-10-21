"""Define the v2.2 Pulse response models.

The models are shared only inside the Pulse service. They keep empty numeric
values as ``None`` so consumers can distinguish missing data from a measured
zero. The module relates to ``main.py`` and ``repository.py`` and is used to
validate the five operational metric response shapes.
"""

from pydantic import BaseModel, Field, model_validator

from libs.contracts.pulse import is_low_sample


class DescriptiveStats(BaseModel):
    """Describe a sample with percentiles and robust dispersion (REQ-LNS-003)."""

    n: int = Field(ge=0)
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    mad: float | None = None
    iqr: float | None = None


class Concentration(BaseModel):
    """Describe how concentrated a set of magnitudes is (REQ-LNS-003)."""

    n: int = Field(ge=0)
    hhi: float | None = Field(default=None, ge=0, le=1)
    gini: float | None = Field(default=None, ge=0, le=1)


class BurnVersusPlan(BaseModel):
    """Compare actual burn with planned burn over the projects that report both."""

    n: int = Field(ge=0)
    planned: float | None = None
    actual: float | None = None
    variance: float | None = None
    ratio: float | None = None


class ServiceLineValue(BaseModel):
    """Represent pipeline value for one service line and its share of the total."""

    service_line: str
    value: float
    projects: int = Field(ge=0)
    share: float | None = Field(default=None, ge=0, le=1)


class StageDuration(BaseModel):
    """Represent the median days projects spent in one funnel stage."""

    stage: str
    median_days: float | None = Field(default=None, ge=0)
    n: int = Field(ge=0)


class SignalPoint(BaseModel):
    """Represent one unprocessed-signal time bucket."""

    t: str
    total: int = Field(ge=0)
    failed: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0, le=1)


class UnprocessedSignalsResponse(BaseModel):
    """Return signal processing points and freshness metadata."""

    points: list[SignalPoint]
    as_of: int
    stale: bool
    method_version: str | None = None


class PipelineBacklog(BaseModel):
    """Represent current pipeline capacity when a rollup is available."""

    active: int | None = Field(default=None, ge=0)
    hot: int | None = Field(default=None, ge=0)
    capacity_estimate: int | None = Field(default=None, ge=0)


class PipelineBacklogResponse(PipelineBacklog):
    """Return pipeline backlog metrics and freshness metadata."""

    as_of: int
    stale: bool
    method_version: str | None = None


class SourceEfficiency(BaseModel):
    """Represent source cost and conversion metrics."""

    name: str
    cost_usd: str | None = None
    leads: int = Field(ge=0)
    meetings: int = Field(ge=0)
    cost_per_meeting: str | None = None
    roas: float | None = Field(default=None, ge=0)


class SourceEfficiencyResponse(BaseModel):
    """Return source efficiency rows and freshness metadata."""

    sources: list[SourceEfficiency]
    as_of: int
    stale: bool
    method_version: str | None = None
    leads_concentration: Concentration | None = None


class PipelineTimeGroup(BaseModel):
    """Represent percentile timing for one pipeline grouping."""

    key: str
    p50_days: float | None = Field(default=None, ge=0)
    p90_days: float | None = Field(default=None, ge=0)
    n: int = Field(ge=0)
    low_sample: bool = False

    @model_validator(mode="after")
    def _apply_low_sample(self) -> "PipelineTimeGroup":
        """Force the low-sample flag whenever n is below five."""
        if is_low_sample(self.n):
            self.low_sample = True
        return self


class PipelineTimeResponse(BaseModel):
    """Return time-in-pipeline groups and freshness metadata."""

    groups: list[PipelineTimeGroup]
    as_of: int
    stale: bool
    method_version: str | None = None
    p50_days_stats: DescriptiveStats | None = None


class TopSource(BaseModel):
    """Represent one source in the qualified-lead ranking."""

    source: str
    qualified_leads: int = Field(ge=0)
    conversion_rate: float | None = Field(default=None, ge=0, le=1)


class TopSourcesResponse(BaseModel):
    """Return the source ranking and freshness metadata."""

    ranking: list[TopSource]
    as_of: int
    stale: bool
    method_version: str | None = None
    qualified_leads_concentration: Concentration | None = None


class SourceFreshness(BaseModel):
    """Represent the last successful load of one data source."""

    source: str
    as_of: int | None = None
    stale: bool = False


class OpsHealthResponse(BaseModel):
    """Return pipeline freshness per source and the outbox backlog."""

    sources: list[SourceFreshness]
    outbox_backlog: int | None = Field(default=None, ge=0)
    as_of: int
    stale: bool
    method_version: str | None = None


class TerritoryOpportunity(BaseModel):
    """Represent one ranked territory with its composite indices (0..1 in city)."""

    rank: int = Field(ge=1)
    city: str
    territory_code: str
    territory_name: str
    ipet: float = Field(ge=0, le=1)
    imd: float = Field(ge=0, le=1)
    iop: float = Field(ge=0, le=1)
    n_in_city: int = Field(ge=0)
    low_sample: bool = False

    @model_validator(mode="after")
    def _apply_low_sample(self) -> "TerritoryOpportunity":
        """Force the low-sample flag whenever the city has fewer than five territories."""
        if is_low_sample(self.n_in_city):
            self.low_sample = True
        return self


class TerritoryOpportunityResponse(BaseModel):
    """Return ranked territories and freshness metadata."""

    territories: list[TerritoryOpportunity]
    as_of: int
    stale: bool
    method_version: str | None = None
