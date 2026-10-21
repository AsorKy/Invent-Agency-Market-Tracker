"""Hand-computed tests for the metric pack library (REQ-LNS-008).

Each expected value is derived in a comment next to the assertion.
"""

from __future__ import annotations

import math
import subprocess
import sys

import pytest

from libs.domain import metrics as m


def test_roc_auc_basic():
    # Positives {0.35, 0.8}, negatives {0.1, 0.4}: winning pairs 3 of 4 -> 0.75.
    assert m.roc_auc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]) == pytest.approx(0.75)


def test_roc_auc_ties_count_half():
    # All scores equal: every pair ties -> 0.5.
    assert m.roc_auc([0, 1], [0.5, 0.5]) == pytest.approx(0.5)
    # Pairs: p0.5 vs n0.5 tie (0.5), p0.5 vs n0.1 win (1), p0.9 wins both (2) -> 3.5 / 4.
    assert m.roc_auc([1, 0, 1, 0], [0.5, 0.5, 0.9, 0.1]) == pytest.approx(0.875)


def test_roc_auc_perfect_and_inverted():
    assert m.roc_auc([0, 1], [0.1, 0.9]) == pytest.approx(1.0)
    assert m.roc_auc([0, 1], [0.9, 0.1]) == pytest.approx(0.0)


def test_pr_auc_basic():
    # Ranked: 0.8(1), 0.4(0), 0.35(1), 0.1(0). Precision at the positives: 1 and 2/3.
    # Each recall step is 1/2 -> (1 + 2/3) / 2 = 5/6.
    assert m.pr_auc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]) == pytest.approx(5 / 6)


def test_pr_auc_tie_is_order_independent():
    # One threshold holding both items: recall step 1, precision 1/2 -> 0.5.
    assert m.pr_auc([1, 0], [0.3, 0.3]) == pytest.approx(0.5)
    assert m.pr_auc([0, 1], [0.3, 0.3]) == pytest.approx(0.5)


def test_brier_score():
    # (0.9-1)^2 + (0.2-0)^2 + (0.6-1)^2 = 0.01 + 0.04 + 0.16 = 0.21; / 3 = 0.07.
    assert m.brier_score([1, 0, 1], [0.9, 0.2, 0.6]) == pytest.approx(0.07)


def test_reliability_table():
    # Two bins. [0, 0.5): p 0.1, 0.3 -> mean 0.2, labels 0, 1 -> rate 0.5.
    # [0.5, 1]: p 0.6, 0.8, 1.0 -> mean 0.8, labels 1, 0, 1 -> rate 2/3 (1.0 goes to last bin).
    table = m.reliability_table([0, 1, 1, 0, 1], [0.1, 0.3, 0.6, 0.8, 1.0], n_bins=2)
    assert len(table) == 2
    assert table[0] == m.ReliabilityBin(0.0, 0.5, 2, pytest.approx(0.2), pytest.approx(0.5))
    assert table[1] == m.ReliabilityBin(0.5, 1.0, 3, pytest.approx(0.8), pytest.approx(2 / 3))


def test_reliability_table_skips_empty_bins():
    table = m.reliability_table([1, 1], [0.95, 0.91], n_bins=10)
    assert [b.n_items for b in table] == [2]


def test_lift_and_precision_at_k():
    y = [1, 0, 1, 0, 0]
    s = [0.9, 0.8, 0.7, 0.2, 0.1]
    # Top 2 = items 0, 1 -> 1 hit -> precision 0.5; base rate 2/5 -> lift 1.25.
    assert m.precision_at_k(y, s, 2) == pytest.approx(0.5)
    assert m.lift_at_k(y, s, 2) == pytest.approx(1.25)
    # Whole list: precision equals base rate -> lift 1.
    assert m.lift_at_k(y, s, 5) == pytest.approx(1.0)


def test_precision_at_k_by_group():
    y = [0, 1, 1, 0, 0]
    s = [0.9, 0.8, 0.7, 0.2, 0.1]
    g = ["a", "a", "b", "b", "b"]
    # k=1: group a top is 0.9 (label 0) -> 0; group b top is 0.7 (label 1) -> 1.
    assert m.precision_at_k_by_group(y, s, g, 1) == {"a": 0.0, "b": 1.0}
    # k=5: groups smaller than k use all items: a -> 1/2, b -> 1/3.
    result = m.precision_at_k_by_group(y, s, g, 5)
    assert result["a"] == pytest.approx(0.5)
    assert result["b"] == pytest.approx(1 / 3)


def test_ndcg_at_k():
    rel = [3, 2, 0]
    s = [0.1, 0.9, 0.5]
    # Ranked gains: 2, 0, 3. DCG@3 = 2/log2(2) + 0/log2(3) + 3/log2(4) = 3.5.
    # Ideal gains: 3, 2, 0. IDCG = 3 + 2/log2(3).
    ideal = 3 + 2 / math.log2(3)
    assert m.ndcg_at_k(rel, s, 3) == pytest.approx(3.5 / ideal)
    # k=2: DCG = 2 + 0 = 2.
    assert m.ndcg_at_k(rel, s, 2) == pytest.approx(2 / ideal)
    # Perfect ordering -> 1.
    assert m.ndcg_at_k(rel, [0.9, 0.5, 0.1], 3) == pytest.approx(1.0)
    # No relevant item -> 0 by convention.
    assert m.ndcg_at_k([0, 0], [0.2, 0.1], 2) == 0.0


def test_map_and_mrr():
    # Query 1 [1,0,1]: (1/1 + 2/3) / 2 = 5/6. Query 2 [0,1]: (1/2) / 1 = 1/2.
    assert m.mean_average_precision([[1, 0, 1], [0, 1]]) == pytest.approx((5 / 6 + 0.5) / 2)
    # A query without relevant items scores 0: (5/6 + 1/2 + 0) / 3.
    assert m.mean_average_precision([[1, 0, 1], [0, 1], [0, 0]]) == pytest.approx((5 / 6 + 0.5) / 3)
    # First relevant ranks: 1 and 2 -> (1 + 1/2) / 2 = 0.75; third query adds 0.
    assert m.mean_reciprocal_rank([[1, 0, 1], [0, 1]]) == pytest.approx(0.75)
    assert m.mean_reciprocal_rank([[1, 0, 1], [0, 1], [0, 0]]) == pytest.approx(0.5)


def test_smape():
    # Term 1: 200 * 10 / (100 + 110) = 9.5238...; term 2 is 0/0 -> 0. Mean = 4.7619...
    assert m.smape([100, 0], [110, 0]) == pytest.approx(200 * 10 / 210 / 2)
    # Forecast 0 against actual 5: 200 * 5 / 5 = 200 (the maximum).
    assert m.smape([5], [0]) == pytest.approx(200.0)


def test_mase():
    train = [1, 3, 2, 4]
    # Lag-1 diffs: 2, 1, 2 -> scale 5/3. MAE of ([5,6] vs [4,8]) = (1 + 2) / 2 = 1.5.
    assert m.mase([5, 6], [4, 8], train) == pytest.approx(1.5 / (5 / 3))
    # Lag-2 diffs: |2-1| = 1, |4-3| = 1 -> scale 1 -> 1.5.
    assert m.mase([5, 6], [4, 8], train, seasonality=2) == pytest.approx(1.5)


def test_pinball_loss():
    # q=0.9. Error +2: 0.9 * 2 = 1.8. Error -2: 0.1 * 2 = 0.2. Mean = 1.0.
    assert m.pinball_loss([10, 10], [8, 12], 0.9) == pytest.approx(1.0)
    # q=0.5 is half the mean absolute error: (2 + 2) / 2 / 2 = 1.0.
    assert m.pinball_loss([10, 10], [8, 12], 0.5) == pytest.approx(1.0)


def test_interval_coverage():
    # 1 in [0,2] yes; 5 in [0,4] no; 9 in [0,9] yes (inclusive) -> 2/3.
    assert m.interval_coverage([1, 5, 9], [0, 0, 0], [2, 4, 9]) == pytest.approx(2 / 3)


def test_qini_curve_and_coefficient():
    score = [0.9, 0.8, 0.7, 0.6]
    treatment = [1, 0, 1, 0]
    outcome = [1, 0, 0, 1]
    # Gain after k items = Yt - Yc * Nt / Nc: k1 -> 0 (no control yet), k2 -> 1 - 0 = 1,
    # k3 -> 1 - 0 * 2/1 = 1, k4 -> 1 - 1 * 2/2 = 0.
    curve = m.qini_curve(score, treatment, outcome)
    assert curve == [(0.0, 0.0), (0.25, 0.0), (0.5, 1.0), (0.75, 1.0), (1.0, 0.0)]
    # Trapezoids with width 0.25: 0 + 0.125 + 0.25 + 0.125 = 0.5; random line area is 0.
    assert m.qini_coefficient(score, treatment, outcome) == pytest.approx(0.5)


def test_qini_random_line_is_subtracted():
    # Items ranked: treated (y=1) then control (y=0). Gain k1 = 0 (no control yet), k2 = 1.
    # Curve (0,0),(0.5,0),(1,1): area = 0.5*0 + 0.5*(0+1)/2 = 0.25.
    # Random line (0,0)->(1,1) has area 0.5 -> coefficient 0.25 - 0.5 = -0.25.
    assert m.qini_coefficient([0.2, 0.1], [1, 0], [1, 0]) == pytest.approx(-0.25)


@pytest.mark.parametrize(
    ("call", "fragment"),
    [
        (lambda: m.roc_auc([0, 1], [0.1]), "length mismatch"),
        (lambda: m.roc_auc([], []), "empty"),
        (lambda: m.roc_auc([1, 1], [0.2, 0.3]), "single class"),
        (lambda: m.roc_auc([0, 2], [0.2, 0.3]), "0 or 1"),
        (lambda: m.pr_auc([0, 0], [0.2, 0.3]), "positive"),
        (lambda: m.brier_score([1], [1.5]), "[0, 1]"),
        (lambda: m.reliability_table([1], [0.5], n_bins=0), "n_bins"),
        (lambda: m.lift_at_k([1, 0], [0.5, 0.4], 3), "k must"),
        (lambda: m.lift_at_k([0, 0], [0.5, 0.4], 1), "positive"),
        (lambda: m.precision_at_k([1, 0], [0.5, 0.4], 0), "k must"),
        (lambda: m.precision_at_k_by_group([1], [0.5], ["a", "b"], 1), "length mismatch"),
        (lambda: m.ndcg_at_k([1, -1], [0.5, 0.4], 1), "non-negative"),
        (lambda: m.mean_average_precision([]), "empty"),
        (lambda: m.mean_reciprocal_rank([[]]), "empty"),
        (lambda: m.smape([1], [1, 2]), "length mismatch"),
        (lambda: m.mase([1], [1], [5, 5]), "zero"),
        (lambda: m.mase([1], [1], [5], seasonality=1), "train needs"),
        (lambda: m.pinball_loss([1], [1], 1.0), "quantile"),
        (lambda: m.interval_coverage([1], [3], [2]), "lower"),
        (lambda: m.qini_curve([0.1, 0.2], [1, 1], [0, 1]), "single class"),
    ],
)
def test_invalid_input_raises_value_error(call, fragment):
    with pytest.raises(ValueError, match=fragment.replace("[", r"\[").replace("]", r"\]")):
        call()


def test_package_import_does_not_load_numpy():
    code = "import sys; import libs.domain.metrics; sys.exit(int('numpy' in sys.modules))"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
