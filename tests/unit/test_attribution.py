"""Hand-computed tests for attribution models (REQ-LNS-005)."""

from datetime import UTC, datetime, timedelta

import pytest

from libs.domain.attribution import (
    AttributionError,
    Journey,
    Touch,
    first_touch,
    last_touch,
    markov,
    shapley,
    time_decay,
)
from services.ml.app.attribution_service import attribute

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def jr(chans: list[str], converted: bool = True, days: list[float] | None = None) -> Journey:
    days = days or list(range(len(chans)))
    return Journey(tuple(Touch(c, T0 + timedelta(days=d)) for c, d in zip(chans, days)), converted)


def test_last_and_first_touch() -> None:
    js = [jr(["A", "B"]), jr(["B", "A"]), jr(["C"], False)]
    assert last_touch(js) == {"B": 1.0, "A": 1.0}
    assert first_touch(js) == {"A": 1.0, "B": 1.0}


def test_touch_order_uses_timestamps_not_position() -> None:
    j = Journey((Touch("B", T0 + timedelta(days=5)), Touch("A", T0)), True)
    assert last_touch([j]) == {"B": 1.0}
    assert first_touch([j]) == {"A": 1.0}


def test_time_decay_half_life() -> None:
    # A is 7 days older than B: weights 0.5 and 1 -> 1/3 and 2/3.
    got = time_decay([jr(["A", "B"], days=[0, 7])], half_life_days=7)
    assert got["A"] == pytest.approx(1 / 3)
    assert got["B"] == pytest.approx(2 / 3)
    assert sum(got.values()) == pytest.approx(1.0)
    with pytest.raises(AttributionError):
        time_decay([], half_life_days=0)


def test_markov_hand_example() -> None:
    # Base conversion = 2/3*1/2 + 1/3 = 2/3. Without A: 1/3 -> effect 1/2.
    # Without B: 0 -> effect 1. Credit = 2 * (1/3, 2/3) = 2/3 and 4/3.
    js = [jr(["A", "B"]), jr(["A"], False), jr(["B"])]
    got = markov(js)
    assert got["A"] == pytest.approx(2 / 3)
    assert got["B"] == pytest.approx(4 / 3)
    assert sum(got.values()) == pytest.approx(2.0)


def test_markov_no_closes() -> None:
    assert markov([jr(["A"], False)]) == {}


def _closes(n: int) -> list[Journey]:
    return [jr(["A"]) if i % 2 else jr(["A", "B"]) for i in range(n)]


def test_shapley_gate_flag_and_boundary() -> None:
    with pytest.raises(AttributionError, match="disabled"):
        shapley(_closes(100))
    with pytest.raises(AttributionError, match="100"):
        shapley(_closes(99), enabled=True)
    got = shapley(_closes(100), enabled=True)
    assert sum(got.values()) == pytest.approx(100.0)


def test_shapley_values() -> None:
    # 50 closes on {A}, 50 on {A,B}: v(A)=50, v(AB)=100, v(B)=0.
    # A = (50 + 100) / 2 = 75; B = (0 + 50) / 2 = 25.
    got = shapley(_closes(100), enabled=True)
    assert got["A"] == pytest.approx(75.0)
    assert got["B"] == pytest.approx(25.0)


def test_service_rows() -> None:
    rows = attribute([jr(["A", "B"]), jr(["C"], False)], "last_touch")
    assert rows == [{"model": "last_touch", "channel": "B", "credit": 1.0, "conversions": 1}]
    with pytest.raises(AttributionError):
        attribute([], "nope")
    with pytest.raises(AttributionError):
        attribute(_closes(100), "shapley")
