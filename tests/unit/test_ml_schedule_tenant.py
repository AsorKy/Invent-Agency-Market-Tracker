"""The ML schedule builds the pulse publisher with the batch tenant (T-160)."""

import asyncio
import logging
from typing import Any

import pytest

from services.ml.app import schedule
from services.ml.app.publisher import PulsePublisher

TENANT = "11111111-1111-1111-1111-111111111111"


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, *, json: Any, headers: dict[str, str]) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers})
        return FakeResponse()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DRIFT_TENANT_ID", raising=False)
    monkeypatch.delenv("INGEST_TENANT_ID", raising=False)


def _weekly(publisher: PulsePublisher | None) -> Any:
    return schedule.run_weekly(
        publisher,
        run_id="r1",
        rows=[{"id": "a", "a": 1.0, "c": "x"}, {"id": "b", "a": 2.0, "c": "y"}],
        numeric=["a"],
        categorical=["c"],
        k=1,
        edges=[],
        clusterer=lambda *a, **k: [0, 0],
        community_finder=lambda edges: {},
    )


def test_drift_tenant_wins_over_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIFT_TENANT_ID", TENANT)
    monkeypatch.setenv("INGEST_TENANT_ID", "other")
    client = FakeClient()
    publisher = schedule.build_publisher(client, "secret")
    assert publisher is not None
    asyncio.run(asyncio.wait_for(_weekly(publisher), timeout=10))
    assert client.calls
    assert all(c["headers"]["X-Tenant-Id"] == TENANT for c in client.calls)


def test_falls_back_to_ingest_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_TENANT_ID", TENANT)
    client = FakeClient()
    publisher = schedule.build_publisher(client, "secret")
    assert publisher is not None
    asyncio.run(asyncio.wait_for(_weekly(publisher), timeout=10))
    assert client.calls[0]["headers"]["X-Tenant-Id"] == TENANT


def test_no_tenant_logs_and_skips(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClient()
    with caplog.at_level(logging.ERROR):
        publisher = schedule.build_publisher(client, "secret")
        assert publisher is None
        asyncio.run(asyncio.wait_for(_weekly(publisher), timeout=10))
        asyncio.run(
            asyncio.wait_for(
                schedule.run_monthly(publisher, run_id="r2", period="2026-01", documents=["a b"]),
                timeout=10,
            )
        )
    assert client.calls == []
    assert "no batch tenant" in caplog.text


def test_main_without_tenant_returns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        asyncio.run(asyncio.wait_for(schedule.main(), timeout=10))
    assert "schedule skipped" in caplog.text
