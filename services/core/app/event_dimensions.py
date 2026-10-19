"""Rollup dimensions that Core adds to event payloads (REQ-PUL-002, REQ-EVT-005).

Pulse groups ``pulse.daily_rollup`` by city and service line, and counts hot leads. Core owns that
data, so it writes ``city``, ``service_line``, and ``hot`` into the payloads of ``lead.created``
and ``lead.outcome_recorded`` here, and ``city`` and ``service_line`` into ``signal.created`` in
the outbox insert of ``repository.py``, all as additive fields. A missing value is omitted instead
of guessed, and Pulse then falls back to ``unknown``. It relates to ``repository.py``,
``leads.py``, ``actions.py``, and ``services/pulse/app/rollups.py``.
"""

from typing import Any

from libs.domain.pipeline import PipelineStage


def clean(value: object) -> str | None:
    """Return a stripped non-empty string, else None."""
    text = str(value).strip() if value is not None else ""
    return text or None


def is_hot(stage: object) -> bool:
    """True when the lead stage is Hot; accepts the enum or its string value."""
    return str(getattr(stage, "value", stage)) == PipelineStage.HOT.value


def lead_dimensions(lead: dict[str, Any]) -> dict[str, Any]:
    """Return ``city`` (when known), ``service_line``, and ``hot`` for a lead event."""
    out: dict[str, Any] = {
        "service_line": lead.get("service_line"),
        "hot": is_hot(lead.get("stage")),
    }
    city = clean(lead.get("city"))
    if city is not None:
        out["city"] = city
    return out
