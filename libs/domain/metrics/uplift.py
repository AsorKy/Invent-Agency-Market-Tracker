"""Uplift metrics: Qini curve and Qini coefficient.

Pure stdlib functions. Relates to REQ-LNS-008 and ADR-IMT-0002 section 2.3.
"""

from __future__ import annotations

from collections.abc import Sequence

from libs.domain.metrics import _validate as v


def qini_curve(
    uplift_score: Sequence[float],
    treatment: Sequence[int],
    outcome: Sequence[float],
) -> list[tuple[float, float]]:
    """Return Qini curve points (population fraction, incremental gain).

    Items are ranked by descending score (ties keep input order). After the
    first k items the gain is ``Y_t - Y_c * N_t / N_c``: treated outcome sum
    minus control outcome sum scaled to the treated count (0 while no control
    item has been seen). The curve starts at (0.0, 0.0) and ends at fraction 1.0.
    """
    v.require_non_empty("uplift_score", uplift_score)
    v.require_same_length(uplift_score=uplift_score, treatment=treatment, outcome=outcome)
    v.require_binary("treatment", treatment)
    v.require_both_classes("treatment", treatment)
    size = len(uplift_score)
    points = [(0.0, 0.0)]
    treated_n = control_n = 0
    treated_y = control_y = 0.0
    for taken, index in enumerate(v.ranked_order(uplift_score), start=1):
        if treatment[index] == 1:
            treated_n += 1
            treated_y += outcome[index]
        else:
            control_n += 1
            control_y += outcome[index]
        gain = treated_y - control_y * treated_n / control_n if control_n else 0.0
        points.append((taken / size, gain))
    return points


def qini_coefficient(
    uplift_score: Sequence[float],
    treatment: Sequence[int],
    outcome: Sequence[float],
) -> float:
    """Return the area between the Qini curve and the random-targeting line.

    The random line joins (0, 0) to the final curve point. Areas use the
    trapezoid rule over the population fraction; positive means the score ranks
    persuadable items ahead of the rest.
    """
    points = qini_curve(uplift_score, treatment, outcome)
    final_gain = points[-1][1]
    area = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        area += (x1 - x0) * (y0 + y1) / 2.0
    return area - final_gain / 2.0
