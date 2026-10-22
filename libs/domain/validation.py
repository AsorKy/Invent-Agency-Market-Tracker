"""Pure validation metrics V1 to V6 and the threshold evaluator (REQ-LNS-010/011).

Stdlib only. Every function is pure, never raises on empty or degenerate data
and returns ``None`` when a metric is undefined (zero division, no data, no
sign change). Loops are bounded by input size or a fixed iteration cap.
Decision thresholds live in ``validation_thresholds.yaml`` next to this module;
``evaluate`` maps a value to a status without constants in code. Relates to
ADR-IMT-0002 section 2.5, ``libs/domain/drift.py`` and ``libs/domain/metrics``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from libs.domain.drift import psi as _psi
from libs.domain.metrics.ranking import precision_at_k as _precision_at_k

THRESHOLDS_PATH = Path(__file__).with_name("validation_thresholds.yaml")
UNKNOWN = "unknown"
_IRR_ITERATIONS = 200
_IRR_LOW = -0.99
_IRR_HIGH_CAP = 1e6


def _div(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _finite(*values: float | None) -> bool:
    return all(v is not None and math.isfinite(v) for v in values)


# ---------------------------------------------------------------- V1 financial


def npv(rate: float, cash_flows: Sequence[float]) -> float | None:
    """Sum of CF_t / (1 + rate)^t with t = index (t0 undiscounted)."""
    if not cash_flows or not _finite(rate) or rate <= -1:
        return None
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cash_flows))


def irr(cash_flows: Sequence[float]) -> float | None:
    """Rate where NPV is zero, by bounded bisection; None without a sign change."""
    if not cash_flows or not any(c > 0 for c in cash_flows) or not any(c < 0 for c in cash_flows):
        return None
    low, high = _IRR_LOW, 1.0
    f_low = npv(low, cash_flows)
    f_high = npv(high, cash_flows)
    while f_low is not None and f_high is not None and f_low * f_high > 0 and high < _IRR_HIGH_CAP:
        high = high * 2 + 1
        f_high = npv(high, cash_flows)
    if f_low is None or f_high is None or f_low * f_high > 0:
        return None
    for _ in range(_IRR_ITERATIONS):
        mid = (low + high) / 2
        f_mid = npv(mid, cash_flows)
        if f_mid is None:
            return None
        if f_mid == 0:
            return mid
        if f_low * f_mid < 0:
            high = mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


def payback_period(cash_flows: Sequence[float]) -> float | None:
    """Periods until cumulative CF reaches 0, linearly interpolated; None if never."""
    if not cash_flows:
        return None
    cumulative = 0.0
    for t, cf in enumerate(cash_flows):
        previous = cumulative
        cumulative += cf
        if cumulative >= 0:
            if t == 0 or cf == 0:
                return float(t)
            return t - 1 + (-previous) / cf
    return None


def discounted_payback_period(rate: float, cash_flows: Sequence[float]) -> float | None:
    """Payback computed on discounted cash flows (DPV)."""
    if not cash_flows or not _finite(rate) or rate <= -1:
        return None
    return payback_period([cf / (1 + rate) ** t for t, cf in enumerate(cash_flows)])


def emv(outcomes: Sequence[tuple[float, float]]) -> float | None:
    """Expected monetary value of (probability, value) pairs; probabilities must sum to 1."""
    if not outcomes or any(p < 0 or p > 1 for p, _ in outcomes):
        return None
    if not math.isclose(sum(p for p, _ in outcomes), 1.0, abs_tol=1e-9):
        return None
    return sum(p * value for p, value in outcomes)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile, q in [0, 1]; None for empty input."""
    if not values or not 0 <= q <= 1:
        return None
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


# ------------------------------------------------------------ V2 unit economics


def cac(total_acquisition_cost: float, new_customers: float) -> float | None:
    """Customer acquisition cost."""
    return _div(total_acquisition_cost, new_customers)


def gross_margin(revenue: float, cogs: float) -> float | None:
    """(revenue - COGS) / revenue."""
    result = _div(revenue - cogs, revenue)
    return result


def ltv(arpa: float, margin: float, monthly_churn: float) -> float | None:
    """ARPA x gross margin / monthly churn."""
    return _div(arpa * margin, monthly_churn)


def ltv_cac_ratio(ltv_value: float | None, cac_value: float | None) -> float | None:
    """LTV divided by CAC."""
    if ltv_value is None or cac_value is None:
        return None
    return _div(ltv_value, cac_value)


def cac_payback_months(cac_value: float, arpa: float, margin: float) -> float | None:
    """Months of gross monthly flow (ARPA x margin) needed to recover CAC."""
    return _div(cac_value, arpa * margin)


# ------------------------------------------------------------------- V3 funnel


def conversion_rate(converted: float, entered: float) -> float | None:
    """Share of entrants that passed a gate."""
    return _div(converted, entered)


def win_rate(won: float, lost: float) -> float | None:
    """won / (won + lost)."""
    return _div(won, won + lost)


def cycle_time_percentiles(days: Sequence[float]) -> dict[str, float | None]:
    """Cycle time p50 and p90 in days."""
    return {"p50": percentile(days, 0.5), "p90": percentile(days, 0.9)}


def pipeline_velocity(
    deals: float, avg_deal_size: float, win_rate_value: float, cycle_days: float
) -> float | None:
    """(deals x avg deal size x win rate) / sales cycle length."""
    return _div(deals * avg_deal_size * win_rate_value, cycle_days)


def pipeline_coverage(pipeline_value: float, quota: float) -> float | None:
    """Pipeline value divided by quota."""
    return _div(pipeline_value, quota)


# ------------------------------------------------------------------ V4 growth


def net_new_mrr(new: float, expansion: float, contraction: float, churn: float) -> float:
    """new + expansion - contraction - churn."""
    return new + expansion - contraction - churn


def nrr(starting_mrr: float, expansion: float, contraction: float, churned: float) -> float | None:
    """(starting + expansion - contraction - churn) / starting."""
    return _div(starting_mrr + expansion - contraction - churned, starting_mrr)


def grr(starting_mrr: float, contraction: float, churned: float) -> float | None:
    """NRR without expansion."""
    return _div(starting_mrr - contraction - churned, starting_mrr)


def churn_rate(lost: float, starting: float) -> float | None:
    """Logo or revenue churn: lost / starting."""
    return _div(lost, starting)


def rule_of_40(growth_pct: float, profit_margin_pct: float) -> float:
    """Growth % plus profit margin % (both in percent points)."""
    return growth_pct + profit_margin_pct


def burn_multiple(net_burn: float, net_new_arr: float) -> float | None:
    """Net burn / net new ARR; undefined when ARR did not grow."""
    if net_new_arr <= 0:
        return None
    return net_burn / net_new_arr


def magic_number(delta_arr: float, prior_quarter_sm: float) -> float | None:
    """Change in ARR / S&M spend of the previous quarter."""
    return _div(delta_arr, prior_quarter_sm)


def quick_ratio(new: float, expansion: float, contraction: float, churn: float) -> float | None:
    """(new + expansion) / (contraction + churn)."""
    return _div(new + expansion, contraction + churn)


# ---------------------------------------------------------------- V5 portfolio


def horizon_mix(revenue_by_horizon: Mapping[str, float]) -> dict[str, float] | None:
    """Revenue share per horizon; None when total revenue is not positive."""
    total = sum(revenue_by_horizon.values())
    if total <= 0:
        return None
    return {h: v / total for h, v in revenue_by_horizon.items()}


def horizon_mix_deviation(
    mix: Mapping[str, float] | None, target: Mapping[str, float] | None = None
) -> float | None:
    """Largest absolute gap between the mix and the configured target mix."""
    if mix is None:
        return None
    goal = target if target is not None else load_thresholds()["horizon_mix_target"]
    return max(abs(mix.get(h, 0.0) - share) for h, share in goal.items())


def hhi(values: Sequence[float]) -> float | None:
    """Herfindahl-Hirschman index on shares, scaled 0 to 1."""
    total = sum(values)
    if not values or total <= 0:
        return None
    return sum((v / total) ** 2 for v in values)


def sharpe_like(npvs: Sequence[float], baseline: float = 0.0) -> float | None:
    """(mean NPV - baseline) / sample std of NPV; None below 2 points or zero spread."""
    if len(npvs) < 2:
        return None
    mean = sum(npvs) / len(npvs)
    std = math.sqrt(sum((x - mean) ** 2 for x in npvs) / (len(npvs) - 1))
    return _div(mean - baseline, std)


# ---------------------------------------------------------------- V6 meta-ML


def model_utility(
    treated_pre: float, treated_post: float, baseline_pre: float, baseline_post: float
) -> float:
    """Difference-in-differences of value (USD) against the baseline group."""
    return (treated_post - treated_pre) - (baseline_post - baseline_pre)


def decision_latency_seconds(
    pairs: Sequence[tuple[datetime, datetime]],
) -> dict[str, float | None]:
    """p50 and p90 seconds between candidate presented and human confirmation."""
    seconds = [(done - start).total_seconds() for start, done in pairs if done >= start]
    return {"p50": percentile(seconds, 0.5), "p90": percentile(seconds, 0.9)}


def drift_score(reference: Sequence[float], current: Sequence[float]) -> float | None:
    """PSI of current against reference; None on empty samples."""
    if not reference or not current:
        return None
    return _psi(reference, current)


def precision_at_k_real(closed: Sequence[int], scores: Sequence[float], k: int) -> float | None:
    """Of the top-K scored (hot) items, the share that really closed."""
    if not closed or len(closed) != len(scores) or k < 1 or k > len(closed):
        return None
    if any(c not in (0, 1) for c in closed):
        return None
    return _precision_at_k(closed, scores, k)


def funnel_lift(rate_after: float, rate_before: float) -> float | None:
    """Y / X: rate after the system over the historical baseline."""
    return _div(rate_after, rate_before)


# ------------------------------------------------------------------ thresholds


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or "metrics" not in data:
        raise ValueError("thresholds file must define 'metrics'")
    return data


def load_thresholds(path: Path | str | None = None) -> dict[str, Any]:
    """Load the thresholds YAML (default: the file beside this module)."""
    return _load(str(path or THRESHOLDS_PATH))


def evaluate(
    metric: str,
    value: float | None,
    *,
    variant: str | None = None,
    thresholds: Mapping[str, Any] | None = None,
) -> str:
    """Return the status for a metric value; ``unknown`` if value or rule is missing."""
    if value is None or not math.isfinite(value):
        return UNKNOWN
    config = thresholds if thresholds is not None else load_thresholds()
    rule = config.get("metrics", {}).get(metric)
    if rule is None:
        return UNKNOWN
    if "variants" in rule:
        rule = rule["variants"].get(variant) if variant else None
        if rule is None:
            return UNKNOWN
    for band in rule.get("bands", []):
        if "lt" in band and not value < band["lt"]:
            continue
        if "le" in band and not value <= band["le"]:
            continue
        return str(band["status"])
    return UNKNOWN
