"""Tests for the validation threshold loader and evaluator (REQ-LNS-011)."""

import pytest

from libs.domain import validation as v


@pytest.mark.parametrize(
    ("metric", "value", "variant", "status"),
    [
        ("npv_p10", 1, None, "healthy"),
        ("npv_p10", 0, None, "watch"),
        ("irr", 0.10, None, "watch"),
        ("irr", 0.20, None, "healthy"),
        ("payback_months", 17, "H1", "healthy"),
        ("payback_months", 18, "H1", "watch"),
        ("payback_months", 20, "H2", "healthy"),
        ("payback_months", 30, "H3", "relaxed"),
        ("payback_months", 5, None, "unknown"),
        ("ltv_cac", 0.5, None, "kill"),
        ("ltv_cac", 1, None, "watch"),
        ("ltv_cac", 3, None, "healthy"),
        ("ltv_cac", 5, None, "healthy"),
        ("ltv_cac", 5.1, None, "under_investing"),
        ("cac_payback_months", 11, None, "healthy"),
        ("cac_payback_months", 15, None, "watch"),
        ("cac_payback_months", 19, None, "kill"),
        ("pipeline_coverage", 2, None, "watch"),
        ("pipeline_coverage", 3.5, None, "healthy"),
        ("burn_multiple", 0.5, None, "healthy"),
        ("burn_multiple", 2.5, None, "watch"),
        ("burn_multiple", 3.1, None, "kill"),
        ("psi", 0.05, None, "healthy"),
        ("psi", 0.25, None, "watch"),
        ("psi", 0.3, None, "kill"),
        ("horizon_mix_deviation", 0.05, None, "healthy"),
        ("horizon_mix_deviation", 0.5, None, "kill"),
        ("ltv_cac", None, None, "unknown"),
        ("ltv_cac", float("nan"), None, "unknown"),
        ("no_such_metric", 1, None, "unknown"),
    ],
)
def test_evaluate(metric: str, value: float | None, variant: str | None, status: str) -> None:
    assert v.evaluate(metric, value, variant=variant) == status


def test_loader_defaults_and_custom_thresholds() -> None:
    data = v.load_thresholds()
    assert data["horizon_mix_target"] == {"H1": 0.6, "H2": 0.3, "H3": 0.1}
    custom = {"metrics": {"x": {"bands": [{"lt": 2, "status": "watch"}, {"status": "healthy"}]}}}
    assert v.evaluate("x", 1, thresholds=custom) == "watch"
    assert v.evaluate("x", 2, thresholds=custom) == "healthy"
    assert v.THRESHOLDS_PATH.exists()


def test_loader_rejects_bad_file(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        v.load_thresholds(bad)
