"""Tests for libs.domain.causal on simulated data with known effects."""

import math
import random

import pytest

from libs.domain import causal


def _panel(rng, n, periods, start, effect, trend=0.5):
    rows = []
    for _ in range(n):
        base = rng.gauss(10, 2)
        rows.append(
            [
                base + trend * p + rng.gauss(0, 0.3) + (effect if p >= start else 0)
                for p in range(periods)
            ]
        )
    return rows


def test_did_hand_computed_2x2():
    treated = [[10.0, 16.0], [12.0, 18.0]]  # diff +6
    control = [[8.0, 10.0], [10.0, 12.0]]  # diff +2
    r = causal.difference_in_differences(treated, control, 1)
    assert r.ate == pytest.approx(4.0)
    assert r.att == pytest.approx(4.0)
    assert r.atu is None
    assert r.status == "ok"


def test_did_recovers_effect():
    rng = random.Random(1)
    t = _panel(rng, 60, 8, 4, 3.0)
    c = _panel(rng, 60, 8, 4, 0.0)
    r = causal.difference_in_differences(t, c, 4)
    assert abs(r.ate - 3.0) < 0.3
    assert r.ci_low < 3.0 < r.ci_high
    assert r.p_value < 0.001
    assert r.e_value > 1
    assert r.status == "ok"


def test_synthetic_weights_simplex():
    rng = random.Random(2)
    donors = [[rng.gauss(0, 1) for _ in range(10)] for _ in range(5)]
    w = causal.synthetic_weights(donors[0], donors)
    assert all(x >= 0 for x in w)
    assert sum(w) == pytest.approx(1.0)
    assert w[0] > 0.9


def test_synthetic_control_recovers_effect():
    rng = random.Random(3)
    common = [math.sin(i / 2) * 3 for i in range(16)]
    donors = [[common[i] + k + rng.gauss(0, 0.1) for i in range(16)] for k in range(8)]
    treated = [common[i] + 3.5 + rng.gauss(0, 0.1) + (4.0 if i >= 10 else 0) for i in range(16)]
    r = causal.synthetic_control(treated, donors, 10)
    assert abs(r.ate - 4.0) < 0.5
    assert r.atu is None and r.att == r.ate


def test_psm_recovers_effect_with_confounding():
    rng = random.Random(4)
    x, t, y = [], [], []
    for _ in range(600):
        a, b = rng.gauss(0, 1), rng.gauss(0, 1)
        p = 1 / (1 + math.exp(-(0.8 * a - 0.5 * b)))
        ti = 1 if rng.random() < p else 0
        x.append([a, b])
        t.append(ti)
        y.append(2.0 * ti + 1.5 * a + b + rng.gauss(0, 0.5))
    r = causal.propensity_matching(x, t, y)
    assert abs(r.ate - 2.0) < 0.5
    assert r.att is not None and r.atu is not None
    assert r.ci_low < r.ate < r.ci_high


def test_sign_flip_is_inconclusive():
    # Mean DID is slightly positive but driven by one outlier; median is negative.
    treated = [[0.0, -1.0], [0.0, -1.0], [0.0, -1.0], [0.0, 10.0]]
    control = [[0.0, 0.0]] * 4
    r = causal.difference_in_differences(treated, control, 1)
    assert r.ate > 0
    assert r.status == "inconclusive"
    assert any(c.flips_sign for c in r.robustness)


def test_e_value_known_points():
    assert causal.e_value(0.0) == 1.0
    rr = math.exp(0.91 * 0.5)
    assert causal.e_value(-0.5) == pytest.approx(rr + math.sqrt(rr * (rr - 1)))


def test_invalid_inputs():
    with pytest.raises(ValueError):
        causal.difference_in_differences([], [[1.0, 2.0]], 1)
