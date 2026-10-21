"""Forecast metrics: sMAPE, MASE, pinball loss, interval coverage.

Pure stdlib functions over actual and forecast sequences. Relates to
REQ-LNS-008 and ADR-IMT-0002 section 2.3.
"""

from __future__ import annotations

from collections.abc import Sequence

from libs.domain.metrics import _validate as v


def smape(actual: Sequence[float], forecast: Sequence[float]) -> float:
    """Return symmetric MAPE in percent (range 0 to 200).

    Each term is 200 * |f - a| / (|a| + |f|); a term with a = f = 0 counts as 0.
    """
    v.require_non_empty("actual", actual)
    v.require_same_length(actual=actual, forecast=forecast)
    total = 0.0
    for a, f in zip(actual, forecast, strict=True):
        denominator = abs(a) + abs(f)
        if denominator:
            total += 200.0 * abs(f - a) / denominator
    return total / len(actual)


def mase(
    actual: Sequence[float],
    forecast: Sequence[float],
    train: Sequence[float],
    seasonality: int = 1,
) -> float:
    """Return the mean absolute scaled error against the seasonal naive baseline.

    The scale is the in-sample mean absolute difference of ``train`` at lag
    ``seasonality`` (1 = plain naive). Below 1.0 beats the baseline.
    """
    v.require_non_empty("actual", actual)
    v.require_same_length(actual=actual, forecast=forecast)
    if isinstance(seasonality, bool) or not isinstance(seasonality, int) or seasonality < 1:
        raise ValueError(f"seasonality must be a positive integer, got {seasonality!r}")
    if len(train) <= seasonality:
        raise ValueError(
            f"train needs more than seasonality={seasonality} points, got {len(train)}"
        )
    scale = sum(abs(train[i] - train[i - seasonality]) for i in range(seasonality, len(train)))
    scale /= len(train) - seasonality
    if scale == 0:
        raise ValueError("train is constant at this seasonality; MASE scale is zero")
    mae = sum(abs(a - f) for a, f in zip(actual, forecast, strict=True)) / len(actual)
    return mae / scale


def pinball_loss(actual: Sequence[float], forecast: Sequence[float], quantile: float) -> float:
    """Return the mean pinball (quantile) loss for one quantile in (0, 1)."""
    v.require_non_empty("actual", actual)
    v.require_same_length(actual=actual, forecast=forecast)
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"quantile must lie strictly between 0 and 1, got {quantile!r}")
    total = 0.0
    for a, f in zip(actual, forecast, strict=True):
        error = a - f
        total += max(quantile * error, (quantile - 1.0) * error)
    return total / len(actual)


def interval_coverage(
    actual: Sequence[float], lower: Sequence[float], upper: Sequence[float]
) -> float:
    """Return the share of actuals inside [lower, upper] (bounds inclusive)."""
    v.require_non_empty("actual", actual)
    v.require_same_length(actual=actual, lower=lower, upper=upper)
    if any(lo > hi for lo, hi in zip(lower, upper, strict=True)):
        raise ValueError("lower must not exceed upper")
    inside = sum(1 for a, lo, hi in zip(actual, lower, upper, strict=True) if lo <= a <= hi)
    return inside / len(actual)
