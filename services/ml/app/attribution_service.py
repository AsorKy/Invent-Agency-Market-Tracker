"""Service-layer attribution runner (REQ-LNS-005).

Takes journeys, runs one model from ``libs.domain.attribution``, and returns
rows shaped for ``pulse.attribution``. Persistence is not done here; a later
task publishes the rows. It relates to ``libs/domain/attribution.py``.
"""

from collections.abc import Sequence
from typing import Any

from libs.domain import attribution as attr

MODELS = ("last_touch", "first_touch", "time_decay", "markov", "shapley")


def attribute(
    journeys: Sequence[attr.Journey],
    model: str,
    *,
    half_life_days: float = 7.0,
    shapley_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Return one row per channel: model, channel, credit, and total closes."""
    if model == "last_touch":
        credit = attr.last_touch(journeys)
    elif model == "first_touch":
        credit = attr.first_touch(journeys)
    elif model == "time_decay":
        credit = attr.time_decay(journeys, half_life_days)
    elif model == "markov":
        credit = attr.markov(journeys)
    elif model == "shapley":
        credit = attr.shapley(journeys, enabled=shapley_enabled)
    else:
        raise attr.AttributionError(f"unknown attribution model: {model}")
    closes = attr.count_closes(journeys)
    return [
        {"model": model, "channel": c, "credit": round(v, 9), "conversions": closes}
        for c, v in sorted(credit.items())
    ]
