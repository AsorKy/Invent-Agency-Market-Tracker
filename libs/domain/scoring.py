"""Implement the deterministic and auditable F1 scoring baseline.

The function weights ICP fit, signal strength, and territory opportunity into a
score from 0 to 100 and marks scores at or above ``HOT_SCORE_THRESHOLD`` as
hot. It relates to ``services/ml/app/main.py`` and ``ml.lead_score``; future
supervised versions can retain this breakdown as a baseline. It is used to
justify, prioritize, or reject opportunities with a traceable rule.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

HOT_SCORE_THRESHOLD = 75
RULE_NAMES = ("icp_fit", "signal_strength", "territory")
RULE_VERSION = "rule-f1"


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    """Relative weights of the three components; only their ratio matters."""

    icp_fit: float = 45.0
    signal_strength: float = 35.0
    territory: float = 20.0

    def __post_init__(self) -> None:
        values = (self.icp_fit, self.signal_strength, self.territory)
        if any(value < 0 for value in values) or sum(values) <= 0:
            raise ValueError("score weights must be non-negative with a positive sum")


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    score: int
    icp_component: int
    signal_component: int
    territory_component: int
    is_hot: bool


def resolve_weights(
    rules: Iterable[tuple[str, Decimal | float, bool]] = (),
    tenant_config: Mapping[str, object] | None = None,
) -> ScoreWeights:
    """Build weights from ``core.lead_score_rule`` rows and tenant overrides.

    Each rule is ``(rule_name, weight, active)``. Inactive and unknown rules are
    ignored. Tenant config key ``score_weights`` (a mapping of rule name to
    number) overrides the rows. Missing names keep the F1 defaults.
    """
    defaults = ScoreWeights()
    values = {
        "icp_fit": defaults.icp_fit,
        "signal_strength": defaults.signal_strength,
        "territory": defaults.territory,
    }
    for name, weight, active in rules:
        if active and name in values:
            values[name] = float(weight)
    overrides = (tenant_config or {}).get("score_weights")
    if isinstance(overrides, Mapping):
        for name, weight in overrides.items():
            if name in values and isinstance(weight, int | float) and not isinstance(weight, bool):
                values[name] = float(weight)
    return ScoreWeights(**values)


def compute_source_version(company_version: int, signal_versions: Iterable[int]) -> str:
    """Return ``company.version + sum(signal.version)`` as text (ADR 10.3)."""
    return str(company_version + sum(signal_versions))


def is_stale(stored_source_version: str | None, current_source_version: str) -> bool:
    """Report whether a stored score no longer matches its sources."""
    return stored_source_version != current_source_version


def calculate_rule_score(
    *,
    icp_fit: float,
    signal_strength: float,
    territory_opportunity: float,
    weights: ScoreWeights | None = None,
) -> ScoreBreakdown:
    values = (icp_fit, signal_strength, territory_opportunity)
    if any(value < 0 or value > 1 for value in values):
        raise ValueError("score inputs must be between 0 and 1")

    active = weights or ScoreWeights()
    total = active.icp_fit + active.signal_strength + active.territory
    icp = round(icp_fit * 100 * active.icp_fit / total)
    signal = round(signal_strength * 100 * active.signal_strength / total)
    territory = round(territory_opportunity * 100 * active.territory / total)
    score = min(100, icp + signal + territory)
    return ScoreBreakdown(
        score=score,
        icp_component=icp,
        signal_component=signal,
        territory_component=territory,
        is_hot=score >= HOT_SCORE_THRESHOLD,
    )
