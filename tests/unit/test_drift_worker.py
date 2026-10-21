"""Tests for the drift worker loader, alert authentication, and nightly loop (REQ-LNS-007).

Uses fake HTTP clients and an injected clock and sleep, so nothing waits or
touches the network.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from libs.common.internal_auth import verify_service_token
from services.ml.app import drift_worker, schedule

SECRET = "test-secret"
TENANT = "00000000-0000-4000-8000-0000000000a1"


class FakeResponse:
    def __init__(self, payload: Any = None, status: int = 200) -> None:
        self._payload, self.status = payload, status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"status {self.status}")

    def json(self) -> Any:
        return self._payload


class FakePulse:
    def __init__(self, points: list[dict[str, Any]], status: int = 200) -> None:
        self.points, self.status, self.calls = points, status, []

    async def get(self, url: str, *, params: Any, headers: dict[str, str]) -> FakeResponse:
        self.calls.append((url, params, headers))
        return FakeResponse({"points": self.points}, self.status)


class FakeWebhook:
    def __init__(self) -> None:
        self.posts: list[tuple[str, Any, dict[str, str]]] = []

    async def post(self, url: str, *, json: Any, headers: dict[str, str]) -> FakeResponse:
        self.posts.append((url, json, headers))
        return FakeResponse()


def _points(values: list[float]) -> list[dict[str, Any]]:
    return [
        {"t": f"w{i}", "total": 100, "failed": int(v * 100), "rate": v}
        for i, v in enumerate(values)
    ]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        drift_worker.WEBHOOK_ENV,
        drift_worker.HEADER_NAME_ENV,
        drift_worker.HEADER_VALUE_ENV,
        *drift_worker.TENANT_ENVS,
    ):
        monkeypatch.delenv(name, raising=False)


async def test_loader_signs_token_sends_tenant_and_splits_windows() -> None:
    pulse = FakePulse(_points([0.1] * 8 + [0.9] * 4))
    samples = await drift_worker.PulseSeriesLoader(pulse, SECRET, TENANT)()
    url, params, headers = pulse.calls[0]
    assert url == drift_worker.SIGNALS_PATH and params == {"granularity": "week"}
    assert headers["X-Tenant-Id"] == TENANT
    assert verify_service_token(headers["X-Service-Token"], SECRET) == "ml"
    reference, current = samples["unprocessed_rate"]
    assert reference == [0.1] * 8 and current == [0.9] * 4


async def test_loader_skips_short_series_and_raises_on_error() -> None:
    assert (
        await drift_worker.PulseSeriesLoader(FakePulse(_points([0.1] * 5)), SECRET, TENANT)() == {}
    )
    with pytest.raises(RuntimeError):
        await drift_worker.PulseSeriesLoader(FakePulse([], status=503), SECRET, TENANT)()


async def test_alert_carries_auth_header_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(drift_worker.WEBHOOK_ENV, "http://n8n/webhook/drift-alert")
    monkeypatch.setenv(drift_worker.HEADER_NAME_ENV, "X-Drift-Auth")
    monkeypatch.setenv(drift_worker.HEADER_VALUE_ENV, "from-env")
    hook = FakeWebhook()
    loader = drift_worker.PulseSeriesLoader(
        FakePulse(_points([0.1, 0.12] * 4 + [0.9] * 4)), SECRET, TENANT
    )
    results = await drift_worker.run_drift(loader, hook, run_id="r1")
    assert any(item.status == "retrain" for item in results)
    url, body, headers = hook.posts[0]
    assert url == "http://n8n/webhook/drift-alert" and headers == {"X-Drift-Auth": "from-env"}
    assert body["run_id"] == "r1" and body["features"]


async def test_alert_refused_without_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(drift_worker.WEBHOOK_ENV, "http://n8n/webhook/drift-alert")
    hook = FakeWebhook()

    async def loader() -> drift_worker.FeatureSamples:
        return {"f": ([0.1, 0.2] * 5, [5.0] * 10)}

    with pytest.raises(drift_worker.DriftConfigError):
        await drift_worker.run_drift(loader, hook, run_id="r1")
    assert hook.posts == []


async def test_no_alert_without_url_or_when_stable() -> None:
    hook = FakeWebhook()

    async def drifted() -> drift_worker.FeatureSamples:
        return {"f": ([0.1, 0.2] * 5, [5.0] * 10)}

    async def stable() -> drift_worker.FeatureSamples:
        return {"f": ([0.1, 0.2] * 5, [0.1, 0.2] * 5)}

    await drift_worker.run_drift(drifted, hook, run_id="r1")
    await drift_worker.run_drift(stable, hook, run_id="r1", url="http://x")
    assert hook.posts == []


def test_tenant_id_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(drift_worker.DriftConfigError):
        drift_worker.tenant_id()
    monkeypatch.setenv("INGEST_TENANT_ID", TENANT)
    assert drift_worker.tenant_id() == TENANT


def test_seconds_until_hour_is_next_occurrence() -> None:
    at = lambda h, m=0: datetime(2026, 1, 1, h, m, tzinfo=UTC)  # noqa: E731
    assert schedule.seconds_until_hour(at(6, 30), 7) == 1800
    assert schedule.seconds_until_hour(at(7), 7) == 86400
    assert schedule.seconds_until_hour(at(8), 7) == 23 * 3600


async def test_nightly_loop_is_bounded_and_survives_failures() -> None:
    sleeps: list[float] = []
    calls: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def loader() -> drift_worker.FeatureSamples:
        calls.append("load")
        if len(calls) == 1:
            raise RuntimeError("pulse down")
        return {}

    ids = iter(["a", "b", "c"])
    runs = await schedule.run_nightly(
        loader,
        FakeWebhook(),
        max_runs=3,
        sleep=fake_sleep,
        now=lambda: datetime(2026, 1, 1, 6, tzinfo=UTC),
        run_id=lambda: next(ids),
    )
    assert runs == 3 and calls == ["load"] * 3 and sleeps == [3600.0] * 3
