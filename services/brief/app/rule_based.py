"""Build deterministic Brief fallback content.

The fallback is always available when no model service is configured. It
relates to ``main.py`` and mirrors the content shape expected from a future ML
summarizer, while deliberately avoiding invented market facts or lead data.
"""

from uuid import UUID

from services.brief.app.models import BriefContent


def build_rule_based_content(company_id: UUID) -> BriefContent:
    """Create stable placeholder content from an opaque company identifier."""
    short_id = company_id.hex[:8]
    return BriefContent(
        headline=f"Company {short_id} requires market evidence review.",
        alerts=[],
        next_steps=[
            "Collect verified company signals.",
            "Confirm RUES coverage before qualification.",
            "Review recent contact activity before proposing a project.",
        ],
        coverage={"company": False, "rues": False, "activity": False},
        activity=[],
    )
