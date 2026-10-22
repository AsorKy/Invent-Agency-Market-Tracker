"""Hand-computed tests for validation metrics V1 to V6 (REQ-LNS-010)."""

from datetime import datetime

import pytest

from libs.domain import validation as v


def test_npv_irr() -> None:
    assert v.npv(0.1, [-100, 110]) == pytest.approx(0.0)
    assert v.npv(0.0, [-100, 50, 60]) == pytest.approx(10)
    assert v.irr([-100, 110]) == pytest.approx(0.10, abs=1e-9)
    assert v.irr([-100, 0, 121]) == pytest.approx(0.10, abs=1e-9)
    assert v.irr([100, 50]) is None
    assert v.irr([]) is None
    assert v.npv(-1, [1]) is None and v.npv(0.1, []) is None


def test_irr_high_and_negative() -> None:
    assert v.irr([-100, 400]) == pytest.approx(3.0, abs=1e-6)
    assert v.irr([-100, 50]) == pytest.approx(-0.5, abs=1e-6)


def test_payback() -> None:
    assert v.payback_period([-100, 50, 50]) == pytest.approx(2.0)
    assert v.payback_period([-100, 40, 80]) == pytest.approx(1.75)
    assert v.payback_period([-100, 10]) is None
    assert v.payback_period([]) is None
    assert v.payback_period([5]) == 0.0
    # discounted at 100%: flows -100, 100, 100 -> -100, 50, 25 never recovers
    assert v.discounted_payback_period(1.0, [-100, 100, 100]) is None
    # at 10%: -100, 90.909.., 82.64.. cumulative -9.09 then +73.55
    expected = 1 + 9.0909090909 / 82.6446281
    assert v.discounted_payback_period(0.1, [-100, 100, 100]) == pytest.approx(expected, rel=1e-6)


def test_emv_percentile() -> None:
    assert v.emv([(0.5, 100), (0.3, 0), (0.2, -50)]) == pytest.approx(40)
    assert v.emv([(0.5, 100)]) is None
    assert v.emv([]) is None
    assert v.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert v.percentile([], 0.5) is None


def test_unit_economics() -> None:
    assert v.cac(1000, 10) == 100
    assert v.cac(1000, 0) is None
    assert v.gross_margin(200, 50) == 0.75
    assert v.gross_margin(0, 0) is None
    assert v.ltv(100, 0.8, 0.05) == pytest.approx(1600)
    assert v.ltv(100, 0.8, 0) is None
    assert v.ltv_cac_ratio(1600, 400) == 4
    assert v.ltv_cac_ratio(None, 400) is None
    assert v.cac_payback_months(400, 100, 0.8) == 5
    assert v.cac_payback_months(400, 0, 0.8) is None


def test_funnel() -> None:
    assert v.conversion_rate(25, 100) == 0.25
    assert v.conversion_rate(1, 0) is None
    assert v.win_rate(3, 7) == 0.3
    assert v.win_rate(0, 0) is None
    assert v.cycle_time_percentiles([10, 20, 30, 40, 50]) == {"p50": 30, "p90": 46}
    assert v.cycle_time_percentiles([]) == {"p50": None, "p90": None}
    assert v.pipeline_velocity(10, 1000, 0.3, 30) == 100
    assert v.pipeline_velocity(10, 1000, 0.3, 0) is None
    assert v.pipeline_coverage(300, 100) == 3
    assert v.pipeline_coverage(300, 0) is None


def test_growth() -> None:
    assert v.net_new_mrr(100, 20, 10, 30) == 80
    assert v.nrr(1000, 100, 50, 50) == 1.0
    assert v.grr(1000, 50, 50) == 0.9
    assert v.nrr(0, 1, 1, 1) is None
    assert v.churn_rate(5, 100) == 0.05
    assert v.rule_of_40(30, 15) == 45
    assert v.burn_multiple(200, 100) == 2
    assert v.burn_multiple(200, 0) is None
    assert v.burn_multiple(200, -5) is None
    assert v.magic_number(500, 250) == 2
    assert v.magic_number(500, 0) is None
    assert v.quick_ratio(100, 20, 10, 30) == 3
    assert v.quick_ratio(100, 20, 0, 0) is None


def test_portfolio() -> None:
    assert v.horizon_mix({"H1": 60, "H2": 30, "H3": 10}) == pytest.approx(
        {"H1": 0.6, "H2": 0.3, "H3": 0.1}
    )
    assert v.horizon_mix({}) is None
    mix = v.horizon_mix({"H1": 80, "H2": 10, "H3": 10})
    assert v.horizon_mix_deviation(mix) == pytest.approx(0.2)
    assert v.horizon_mix_deviation(None) is None
    assert v.hhi([50, 50]) == 0.5
    assert v.hhi([100]) == 1.0
    assert v.hhi([]) is None
    # mean 20, sample std 10
    assert v.sharpe_like([10, 20, 30]) == pytest.approx(2.0)
    assert v.sharpe_like([10, 20, 30], baseline=10) == pytest.approx(1.0)
    assert v.sharpe_like([5]) is None
    assert v.sharpe_like([5, 5]) is None


def test_meta_ml() -> None:
    assert v.model_utility(100, 150, 100, 120) == 30
    t0 = datetime(2026, 1, 1, 0, 0, 0)
    pairs = [(t0, datetime(2026, 1, 1, 0, 1, 0)), (t0, datetime(2026, 1, 1, 0, 3, 0))]
    assert v.decision_latency_seconds(pairs) == {"p50": 120, "p90": pytest.approx(168)}
    assert v.decision_latency_seconds([]) == {"p50": None, "p90": None}
    sample = [float(i) for i in range(100)]
    assert v.drift_score(sample, sample) == pytest.approx(0.0, abs=1e-9)
    assert (v.drift_score(sample, [x + 80 for x in sample]) or 0) > 0.25
    assert v.drift_score([], sample) is None
    assert v.precision_at_k_real([1, 0, 1, 0], [0.9, 0.8, 0.7, 0.1], 2) == 0.5
    assert v.precision_at_k_real([], [], 1) is None
    assert v.precision_at_k_real([1, 0], [0.5, 0.4], 3) is None
    assert v.precision_at_k_real([2, 0], [0.5, 0.4], 1) is None
    assert v.funnel_lift(0.3, 0.1) == pytest.approx(3.0)
    assert v.funnel_lift(0.3, 0) is None
