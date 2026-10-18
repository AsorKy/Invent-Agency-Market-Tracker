"""Unit tests for the deterministic rule score (REQ-COR-006)."""

from decimal import Decimal

import pytest

from libs.domain.scoring import (
    ScoreWeights,
    calculate_rule_score,
    compute_source_version,
    is_stale,
    resolve_weights,
)


def test_default_weights_match_f1_baseline() -> None:
    result = calculate_rule_score(icp_fit=1, signal_strength=1, territory_opportunity=1)
    assert (result.icp_component, result.signal_component, result.territory_component) == (
        45,
        35,
        20,
    )
    assert result.score == 100
    assert result.is_hot


def test_score_is_deterministic() -> None:
    first = calculate_rule_score(icp_fit=0.37, signal_strength=0.81, territory_opportunity=0.5)
    second = calculate_rule_score(icp_fit=0.37, signal_strength=0.81, territory_opportunity=0.5)
    assert first == second


def test_out_of_range_input_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_rule_score(icp_fit=1.1, signal_strength=0, territory_opportunity=0)


def test_custom_weights_are_normalised() -> None:
    weights = ScoreWeights(icp_fit=2, signal_strength=2, territory=0)
    result = calculate_rule_score(
        icp_fit=1, signal_strength=0.5, territory_opportunity=1, weights=weights
    )
    assert (result.icp_component, result.signal_component, result.territory_component) == (
        50,
        25,
        0,
    )
    assert result.score == 75


def test_invalid_weights_rejected() -> None:
    with pytest.raises(ValueError):
        ScoreWeights(icp_fit=0, signal_strength=0, territory=0)
    with pytest.raises(ValueError):
        ScoreWeights(icp_fit=-1)


def test_resolve_weights_rules_then_tenant_override() -> None:
    rules = [
        ("icp_fit", Decimal("50"), True),
        ("signal_strength", Decimal("10"), False),
        ("unknown", Decimal("99"), True),
    ]
    weights = resolve_weights(
        rules, {"score_weights": {"territory": 5, "bogus": 1, "icp_fit": True}}
    )
    assert weights == ScoreWeights(icp_fit=50, signal_strength=35, territory=5)


def test_resolve_weights_defaults() -> None:
    assert resolve_weights() == ScoreWeights()


def test_source_version_and_stale() -> None:
    assert compute_source_version(3, [1, 2, 4]) == "10"
    assert compute_source_version(3, []) == "3"
    assert not is_stale("10", "10")
    assert is_stale("10", "11")
    assert is_stale(None, "1")
