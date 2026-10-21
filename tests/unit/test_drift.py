"""Hand-computed tests for libs/domain/drift.py and the nightly drift worker."""

import asyncio
import math

import pytest

from libs.domain import drift
from services.ml.app import drift_worker


def test_cusum_upward_shift_resets_after_alarm() -> None:
    # hi: 0, 0, 1.5, 3.0 (>2 alarm at 3, reset), 1.5 -> no alarm
    assert drift.cusum([0, 0, 2, 2, 2], target=0, k=0.5, h=2.0) == [3]


def test_cusum_downward_and_quiet() -> None:
    assert drift.cusum([0, -2, -2, -2], target=0, k=0.5, h=2.0) == [2]
    assert drift.cusum([0.2, -0.2, 0.1], target=0, k=0.5, h=2.0) == []
    with pytest.raises(ValueError):
        drift.cusum([1], target=0, k=0.5, h=0)


def test_ewma_hand_computed() -> None:
    smoothed, alarms = drift.ewma_chart([1, 1], target=0, sigma=1, lam=0.5, width=1.0)
    assert smoothed == [0.5, 0.75]
    # limit_1 = sqrt(0.5/1.5 * (1-0.25)) = 0.5 -> 0.5 > 0.5 false; limit_2 ~ 0.559 -> 0.75 alarms
    assert alarms == [1]


def test_change_points_detects_shift() -> None:
    series = [0.0] * 5 + [10.0] * 5
    assert drift.change_points(series, min_size=2, min_gain=10.0) == [5]
    assert drift.change_points([1.0] * 8, min_size=2, min_gain=0.1) == []


def test_psi_identical_is_zero_and_hand_value() -> None:
    ref = [1.0, 2.0, 3.0, 4.0]
    assert drift.psi(ref, ref, bins=2) == pytest.approx(0.0)
    # edge = median 2.5; ref props 0.5/0.5; current 0.75/0.25
    expected = 0.25 * math.log(1.5) + (-0.25) * math.log(0.5)
    assert drift.psi(ref, [1.0, 2.0, 1.5, 4.0], bins=2) == pytest.approx(expected)


def test_psi_epsilon_smoothing_empty_bin() -> None:
    value = drift.psi([1.0, 2.0, 3.0, 4.0], [1.0, 1.0], bins=2, epsilon=0.01)
    assert value == pytest.approx((1 - 0.5) * math.log(2) + (0.01 - 0.5) * math.log(0.02))


def test_classify_boundaries() -> None:
    assert drift.classify_psi(0.0999) == "stable"
    assert drift.classify_psi(0.1) == "watch"
    assert drift.classify_psi(0.25) == "watch"
    assert drift.classify_psi(0.2501) == "retrain"


class Resp:
    def raise_for_status(self) -> None:
        return None


class Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url: str, *, json: dict) -> Resp:
        self.calls.append((url, json))
        return Resp()


STABLE = [float(i) for i in range(20)]
SHIFTED = [float(i) + 30 for i in range(20)]


def test_worker_alerts_only_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(drift_worker.WEBHOOK_ENV, raising=False)
    client = Client()
    loader = lambda: {"a": (STABLE, STABLE), "b": (STABLE, SHIFTED)}  # noqa: E731
    results = asyncio.run(drift_worker.run_drift(loader, client, run_id="r1", url="http://n8n/x"))
    assert [r.status for r in results] == ["stable", "retrain"]
    assert len(client.calls) == 1
    assert [f["feature"] for f in client.calls[0][1]["features"]] == ["b"]


def test_worker_no_url_or_no_flag_no_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(drift_worker.WEBHOOK_ENV, raising=False)
    client = Client()
    asyncio.run(drift_worker.run_drift(lambda: {"b": (STABLE, SHIFTED)}, client, run_id="r"))
    asyncio.run(
        drift_worker.run_drift(lambda: {"a": (STABLE, STABLE)}, client, run_id="r", url="u")
    )
    assert client.calls == []
    monkeypatch.setenv(drift_worker.WEBHOOK_ENV, "http://n8n/env")
    asyncio.run(drift_worker.run_drift(lambda: {"b": (STABLE, SHIFTED)}, client, run_id="r"))
    assert client.calls[0][0] == "http://n8n/env"
