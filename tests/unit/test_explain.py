"""Hand-computed tests for the Dx4 explanation payload."""

import pytest

from services.ml.app import explain

W = {"icp": 40.0, "signal": 30.0}
BASE = {"icp": 0.5, "signal": 0.5}


def test_linear_contributions_hand_computed() -> None:
    out = explain.linear_contributions(W, {"icp": 0.9, "signal": 0.2}, BASE)
    assert out["icp"] == pytest.approx(16.0)
    assert out["signal"] == pytest.approx(-9.0)
    drivers = explain.top_drivers(out, limit=1)
    assert drivers == [{"feature": "icp", "contribution": out["icp"], "direction": "up"}]


def test_counterfactual_flips_with_single_feature() -> None:
    # score = 40*icp + 30*signal; start 0.5/0.5 -> 35; threshold 50; icp step 0.1 (+4 each)
    fn = lambda v: 40 * v["icp"] + 30 * v["signal"]  # noqa: E731
    res = explain.greedy_counterfactual(
        fn,
        {"icp": 0.5, "signal": 0.5},
        {"icp": (0, 1, 0.1), "signal": (0, 1, 0.1)},
        threshold=50,
    )
    # signal step gives 3, icp gives 4: greedy picks icp: 39, 43, 47, 51 -> 4 steps
    assert res["found"] is True and res["steps"] == 4
    assert res["changes"]["icp"]["to"] == pytest.approx(0.9)
    assert "signal" not in res["changes"]


def test_counterfactual_bounded_and_unreachable() -> None:
    fn = lambda v: 10 * v["a"]  # noqa: E731
    res = explain.greedy_counterfactual(fn, {"a": 0.0}, {"a": (0, 1, 0.5)}, threshold=50)
    assert res["found"] is False and res["steps"] == 2
    capped = explain.greedy_counterfactual(
        fn, {"a": 0.0}, {"a": (0, 100, 1)}, threshold=50, max_steps=3
    )
    assert capped["found"] is False and capped["steps"] == 3


def test_counterfactual_downward() -> None:
    fn = lambda v: 10 * v["a"]  # noqa: E731
    res = explain.greedy_counterfactual(
        fn, {"a": 8.0}, {"a": (0, 10, 1)}, threshold=75, max_steps=5
    )
    assert res["found"] is True and res["steps"] == 1


def test_build_explanation_payload() -> None:
    payload = explain.build_explanation(
        W, {"icp": 0.5, "signal": 0.5}, BASE, {"icp": (0, 1, 0.1)}, threshold=40
    )
    assert payload["method"] == "linear"
    assert payload["score"] == pytest.approx(35.0)
    assert payload["counterfactual"]["found"] is True


def test_shap_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "shap", None)
    with pytest.raises(explain.MissingDependency):
        explain.load_shap()
