"""Pure drift and change-point functions for the nightly Dx3 worker (REQ-LNS-007).

Implements a two-sided CUSUM, an EWMA control chart, a mean-shift change-point
detector by binary segmentation, and the Population Stability Index (PSI) with
quantile bins taken from the reference sample. PSI is classified as ``stable``
below 0.1, ``watch`` from 0.1 up to and including 0.25, and ``retrain`` above
0.25: exactly 0.1 is ``watch`` and exactly 0.25 is ``watch``. It relates to
``services/ml/app/drift_worker.py``. All loops are bounded by input sizes.
"""

import math
from collections.abc import Sequence

PSI_WATCH = 0.1
PSI_RETRAIN = 0.25
PSI_EPSILON = 1e-4


def cusum(values: Sequence[float], *, target: float, k: float, h: float) -> list[int]:
    """Two-sided CUSUM; return indices where either sum exceeds ``h``.

    ``hi = max(0, hi + x - target - k)`` and ``lo = max(0, lo + target - x - k)``.
    Both sums reset to zero after an alarm.
    """
    if k < 0 or h <= 0:
        raise ValueError("k must be non-negative and h positive")
    hi = lo = 0.0
    alarms: list[int] = []
    for index, value in enumerate(values):
        hi = max(0.0, hi + value - target - k)
        lo = max(0.0, lo + target - value - k)
        if hi > h or lo > h:
            alarms.append(index)
            hi = lo = 0.0
    return alarms


def ewma_chart(
    values: Sequence[float], *, target: float, sigma: float, lam: float = 0.2, width: float = 3.0
) -> tuple[list[float], list[int]]:
    """EWMA control chart; return the smoothed series and out-of-control indices.

    ``z_i = lam*x_i + (1-lam)*z_{i-1}`` with ``z_0 = target``; the limit is
    ``target +- width*sigma*sqrt(lam/(2-lam)*(1-(1-lam)^(2i)))`` for i from 1.
    """
    if not 0 < lam <= 1 or sigma <= 0 or width <= 0:
        raise ValueError("lam must be in (0, 1], sigma and width positive")
    z = target
    smoothed: list[float] = []
    alarms: list[int] = []
    for index, value in enumerate(values):
        z = lam * value + (1 - lam) * z
        smoothed.append(z)
        limit = width * sigma * math.sqrt(lam / (2 - lam) * (1 - (1 - lam) ** (2 * (index + 1))))
        if abs(z - target) > limit:
            alarms.append(index)
    return smoothed, alarms


def _best_split(values: Sequence[float], lo: int, hi: int, min_size: int) -> tuple[int, float]:
    """Return the split index in ``[lo, hi)`` with the largest SSE reduction."""
    segment = values[lo:hi]
    total = sum(segment)
    n = len(segment)
    whole = total * total / n
    best_index, best_gain = -1, 0.0
    left_sum = 0.0
    for offset in range(1, n):
        left_sum += segment[offset - 1]
        if offset < min_size or n - offset < min_size:
            continue
        right_sum = total - left_sum
        gain = left_sum * left_sum / offset + right_sum * right_sum / (n - offset) - whole
        if gain > best_gain:
            best_index, best_gain = lo + offset, gain
    return best_index, best_gain


def change_points(
    values: Sequence[float], *, min_size: int = 3, min_gain: float, max_points: int = 10
) -> list[int]:
    """Detect mean shifts by binary segmentation; return sorted start indices.

    A split is kept when it reduces the sum of squared errors by at least
    ``min_gain``. At most ``max_points`` points are returned.
    """
    if min_size < 1 or max_points < 0:
        raise ValueError("min_size must be positive and max_points non-negative")
    found: list[int] = []
    segments = [(0, len(values))]
    for _ in range(max_points):
        candidates = [
            (gain, index, seg)
            for seg in segments
            if seg[1] - seg[0] >= 2 * min_size
            for index, gain in [_best_split(values, seg[0], seg[1], min_size)]
            if index >= 0
        ]
        if not candidates:
            break
        gain, index, seg = max(candidates)
        if gain < min_gain:
            break
        found.append(index)
        segments.remove(seg)
        segments.extend([(seg[0], index), (index, seg[1])])
    return sorted(found)


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def psi(
    reference: Sequence[float],
    current: Sequence[float],
    *,
    bins: int = 10,
    epsilon: float = PSI_EPSILON,
) -> float:
    """PSI of ``current`` against ``reference`` using reference quantile bins.

    ``sum((c_i - r_i) * ln(c_i / r_i))`` where proportions below ``epsilon``
    are replaced by ``epsilon``. Interior edges are the distinct reference
    quantiles; values fall in the first bin whose upper edge is >= the value.
    """
    if not reference or not current or bins < 2:
        raise ValueError("reference and current must be non-empty and bins at least 2")
    ordered = sorted(reference)
    edges = sorted({_quantile(ordered, i / bins) for i in range(1, bins)})

    def proportions(sample: Sequence[float]) -> list[float]:
        counts = [0] * (len(edges) + 1)
        for value in sample:
            slot = sum(1 for edge in edges if value > edge)
            counts[slot] += 1
        return [max(count / len(sample), epsilon) for count in counts]

    ref_p = proportions(reference)
    cur_p = proportions(current)
    return sum((c - r) * math.log(c / r) for r, c in zip(ref_p, cur_p, strict=True))


def classify_psi(value: float) -> str:
    """Return ``stable`` (< 0.1), ``watch`` (0.1 to 0.25 inclusive) or ``retrain`` (> 0.25)."""
    if value < PSI_WATCH:
        return "stable"
    if value <= PSI_RETRAIN:
        return "watch"
    return "retrain"
