"""Define Brief digest models for the v2.2 response seam.

The models describe the stable digest envelope while the persistent
``brief.record_digest`` table is still pending. They relate to ``main.py``,
``repository.py``, and ``rule_based.py`` and keep the fallback response
deterministic and explicit about degraded generation.
"""

from uuid import UUID

from pydantic import BaseModel, Field


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


class StoredDigest(BaseModel):
    """Represent a digest persisted by the service-local repository seam."""

    company_id: UUID
    source_version: int = Field(ge=1)
    content: BriefContent
    generated_at: int
    summarizer_version: str
    degraded: bool


class BriefResponse(StoredDigest):
    """Return a digest with stale-while-revalidate metadata."""

    lead_id: UUID | None = None
    stale: bool
