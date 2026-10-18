"""Define Brief digest models for the v2.2 response seam.

The models describe the stable digest envelope, the company facts that the
rule-based summarizer reads, and the per-tenant rule set mirrored from
``core.tenant_config.brief_rules``. They relate to ``main.py``,
``repository.py``, ``rule_based.py`` and ``core_source.py`` and keep the
fallback response deterministic and explicit about degraded generation.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BriefAlert(BaseModel):
    """Represent one actionable alert in a company digest."""

    code: str
    severity: str
    message: str


class BriefContent(BaseModel):
    """Represent the rule-based or model-generated digest content."""

    headline: str
    alerts: list[BriefAlert]
    next_steps: list[str]
    coverage: dict[str, bool]
    activity: list[dict[str, str]]
    priority: float = 0.0


class StoredDigest(BaseModel):
    """Represent a digest persisted by the service-local repository seam.

    ``config_version`` is the tenant configuration version used to generate the
    digest; a lower value than the current one marks the digest stale (lazy
    invalidation on ``tenant.config_updated``).
    """

    company_id: UUID
    source_version: int = Field(ge=1)
    content: BriefContent
    generated_at: int
    summarizer_version: str
    degraded: bool
    config_version: int = Field(default=0, ge=0)


class BriefResponse(StoredDigest):
    """Return a digest with stale-while-revalidate metadata."""

    lead_id: UUID | None = None
    stale: bool


class AlertRules(BaseModel):
    """Mirror ``core.tenant_config.brief_rules.alert_rules`` with its defaults."""

    model_config = ConfigDict(extra="ignore")

    no_contact_days_warning: int = Field(default=5, ge=0)
    no_contact_days_critical: int = Field(default=15, ge=0)
    require_rues_match: bool = True
    min_score_for_hot: int = Field(default=70, ge=0, le=100)
    max_quarantine_days: int = Field(default=7, ge=0)


class BriefRules(BaseModel):
    """Mirror ``core.tenant_config.brief_rules``: thresholds, weights, disabled codes."""

    model_config = ConfigDict(extra="ignore")

    alert_rules: AlertRules = Field(default_factory=AlertRules)
    weights: dict[str, float] = Field(default_factory=dict)
    disabled_alert_codes: list[str] = Field(default_factory=list)


class CompanyFacts(BaseModel):
    """Describe the company state read from ``core`` for one digest."""

    company_id: UUID
    version: int = Field(default=1, ge=0)
    signal_versions_sum: int = Field(default=0, ge=0)
    score: int | None = Field(default=None, ge=0, le=100)
    sector: str | None = None
    city: str | None = None
    signal_codes: list[str] = Field(default_factory=list)
    rues_status: Literal["matched", "pending", "unmatched", "quarantined"] = "pending"
    rues_attempts: int = Field(default=0, ge=0)
    quarantine_days: int = Field(default=0, ge=0)
    domain_certificate_ok: bool = True
    enrichment_failed: bool = False
    enrichment_flags: list[str] = Field(default_factory=list)
    days_since_contact: int | None = Field(default=None, ge=0)
    overdue_tasks: int = Field(default=0, ge=0)
    activity: list[dict[str, str]] = Field(default_factory=list)

    @property
    def expected_version(self) -> int:
        """Return ``company.version + sum(signal.version)``, at least 1."""
        return max(1, self.version + self.signal_versions_sum)
