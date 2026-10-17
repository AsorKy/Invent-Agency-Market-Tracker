"""Implement the deterministic and auditable F1 scoring baseline.

The function weights ICP fit, signal strength, and territory opportunity into a
score from 0 to 100 and marks scores at or above ``HOT_SCORE_THRESHOLD`` as
hot. It relates to ``services/ml/app/main.py`` and ``ml.lead_score``; future
supervised versions can retain this breakdown as a baseline. It is used to
justify, prioritize, or reject opportunities with a traceable rule.
"""

from dataclasses import dataclass

HOT_SCORE_THRESHOLD = 75


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    score: int
    icp_component: int
    signal_component: int
    territory_component: int
    is_hot: bool


def calculate_rule_score(
    *, icp_fit: float, signal_strength: float, territory_opportunity: float
) -> ScoreBreakdown:
    values = (icp_fit, signal_strength, territory_opportunity)
    if any(value < 0 or value > 1 for value in values):
        raise ValueError("score inputs must be between 0 and 1")

    icp = round(icp_fit * 45)
    signal = round(signal_strength * 35)
    territory = round(territory_opportunity * 20)
    score = min(100, icp + signal + territory)
    return ScoreBreakdown(
        score=score,
        icp_component=icp,
        signal_component=signal,
        territory_component=territory,
        is_hot=score >= HOT_SCORE_THRESHOLD,
    )
