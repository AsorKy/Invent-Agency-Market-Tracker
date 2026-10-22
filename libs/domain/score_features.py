"""Deterministic demo score features of a company (REQ-LNS-007, REQ-ML-001).

Pure functions, no I/O. The three features (``icp_fit``, ``signal_strength``,
``territory_opportunity``) are the demo heuristic of the seed, a placeholder until the real
feature definition is decided. The seed (``ops/seed/enrich.py``) and the core internal route
(``services/core/app/score_features.py``) share this one derivation so the numbers agree.
Complexity is O(n) over the profiles. ``territory_opportunity`` is relative to the companies
passed in, so callers pass the companies of one tenant only.
"""

from dataclasses import dataclass
from typing import Any

SCORE_SOURCE_VERSION = "demo-1"
SIGNALS_FOR_FULL_STRENGTH = 6


@dataclass
class CompanyProfile:
    """What the load delivered for one company; the input of the score features."""

    city: str | None = None
    has_website: bool = False
    has_nit: bool = False
    signals: int = 0


def city_widths(profiles: dict[str, CompanyProfile]) -> tuple[dict[str, int], int]:
    """Return the company count per city and the widest city count (1 when empty)."""
    per_city: dict[str, int] = {}
    for profile in profiles.values():
        per_city[profile.city or ""] = per_city.get(profile.city or "", 0) + 1
    return per_city, max(per_city.values(), default=1)


def features_of(profile: CompanyProfile, city_count: int, widest: int) -> dict[str, float]:
    """The three bounded features (0..1) of one company."""
    return {
        "icp_fit": 0.4 + 0.3 * profile.has_website + 0.3 * profile.has_nit,
        "signal_strength": min(1.0, profile.signals / SIGNALS_FOR_FULL_STRENGTH),
        "territory_opportunity": city_count / widest,
    }


def score_features(profiles: dict[str, CompanyProfile]) -> list[dict[str, Any]]:
    """Deterministic bounded features per company (sorted by id), all within 0..1."""
    per_city, widest = city_widths(profiles)
    rows = []
    for company_id in sorted(profiles):
        profile = profiles[company_id]
        rows.append(
            {
                "company_id": company_id,
                **features_of(profile, per_city[profile.city or ""], widest),
                "source_version": SCORE_SOURCE_VERSION,
            }
        )
    return rows
