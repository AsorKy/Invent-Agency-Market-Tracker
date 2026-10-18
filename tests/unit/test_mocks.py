"""Unit tests for the Bright Data mock in ``services/mocks`` (REQ-ING-007).

Covers trigger, progress, snapshot download, latency, failure rate, and signed
webhooks. Webhook signatures are verified with ``libs/common/security.py``.
Clock, sleeper, random source, and webhook sender are injected, so no network
or real waiting is used.
"""

import json
import random
from typing import Any

import pytest

pytest.importorskip("flask")

from libs.common.security import valid_signed_webhook  # noqa: E402
from services.mocks.app.main import create_app, sign_webhook  # noqa: E402

SECRET = "mock-secret"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def trigger(client: Any, dataset: str = "mock-maps", notify: str | None = None) -> str:
    params = {"dataset_id": dataset}
    if notify:
        params["notify"] = notify
    response = client.post("/datasets/v3/trigger", query_string=params, json=[{"q": "x"}])
    assert response.status_code == 200
    return str(response.get_json()["snapshot_id"])


def test_trigger_progress_and_snapshot_for_maps() -> None:
    client = create_app(records_per_snapshot=2, webhook_secret=SECRET).test_client()
    snapshot_id = trigger(client)
    progress = client.get(f"/datasets/v3/progress/{snapshot_id}").get_json()
    assert progress == {"snapshot_id": snapshot_id, "status": "ready", "records": 2}
    records = client.get(f"/datasets/v3/snapshot/{snapshot_id}?format=json").get_json()
    assert len(records) == 2
    assert {"place_id", "name", "phone_number"} <= set(records[0])


def test_computrabajo_dataset_returns_job_records() -> None:
    client = create_app(records_per_snapshot=1).test_client()
    snapshot_id = trigger(client, dataset="mock-computrabajo")
    records = client.get(f"/datasets/v3/snapshot/{snapshot_id}").get_json()
    assert "job_id" in records[0] and "title" in records[0]


def test_progress_stays_running_until_ready_after() -> None:
    clock = Clock()
    client = create_app(ready_after_s=30, clock=clock).test_client()
    snapshot_id = trigger(client)
    assert client.get(f"/datasets/v3/progress/{snapshot_id}").get_json()["status"] == "running"
    assert client.get(f"/datasets/v3/snapshot/{snapshot_id}").status_code == 202
    clock.now += 31
    assert client.get(f"/datasets/v3/progress/{snapshot_id}").get_json()["status"] == "ready"
    assert client.get(f"/datasets/v3/snapshot/{snapshot_id}").status_code == 200


def test_unknown_snapshot_is_404() -> None:
    client = create_app().test_client()
    assert client.get("/datasets/v3/progress/nope").status_code == 404
    assert client.get("/datasets/v3/snapshot/nope").status_code == 404


def test_latency_is_applied_through_the_sleeper() -> None:
    slept: list[float] = []
    client = create_app(latency_ms=250, sleeper=slept.append).test_client()
    trigger(client)
    assert slept == [0.25]


def test_failure_rate_one_always_fails_and_zero_never_fails() -> None:
    failing = create_app(failure_rate=1.0, rng=random.Random(1)).test_client()
    assert failing.post("/datasets/v3/trigger").status_code == 503
    healthy = create_app(failure_rate=0.0, rng=random.Random(1)).test_client()
    assert healthy.post("/datasets/v3/trigger").status_code == 200


def test_failure_rate_is_probabilistic() -> None:
    client = create_app(failure_rate=0.5, rng=random.Random(7)).test_client()
    codes = [client.post("/datasets/v3/trigger").status_code for _ in range(60)]
    assert 10 < codes.count(503) < 50


def test_signed_webhook_is_delivered_and_verifies() -> None:
    sent: list[tuple[str, bytes, dict[str, str]]] = []

    def sender(url: str, body: bytes, headers: dict[str, str]) -> int:
        sent.append((url, body, headers))
        return 202

    clock = Clock()
    client = create_app(webhook_secret=SECRET, clock=clock, webhook_sender=sender).test_client()
    snapshot_id = trigger(client, notify="http://ingest.local/internal/ingest/webhook/bd")
    result = client.post(f"/mocks/bright-data/webhook/{snapshot_id}").get_json()
    assert result == {"delivered": True, "status_code": 202}
    url, body, headers = sent[0]
    assert url == "http://ingest.local/internal/ingest/webhook/bd"
    assert json.loads(body)["snapshot_id"] == snapshot_id
    assert valid_signed_webhook(
        body,
        signature=headers["X-BrightData-Signature"],
        timestamp=headers["X-BrightData-Timestamp"],
        secret=SECRET,
        replay_window_seconds=300,
        now=clock.now,
    )
    assert not valid_signed_webhook(
        body + b" ",
        signature=headers["X-BrightData-Signature"],
        timestamp=headers["X-BrightData-Timestamp"],
        secret=SECRET,
        replay_window_seconds=300,
        now=clock.now,
    )


def test_webhook_requires_notify_url_and_ready_snapshot() -> None:
    clock = Clock()
    client = create_app(ready_after_s=10, clock=clock, webhook_sender=lambda *_: 200).test_client()
    plain = trigger(client)
    clock.now += 11
    assert client.post(f"/mocks/bright-data/webhook/{plain}").status_code == 409
    pending = trigger(client, notify="http://x.local/hook")
    assert client.post(f"/mocks/bright-data/webhook/{pending}").status_code == 409
    assert client.post("/mocks/bright-data/webhook/nope").status_code == 404


def test_sign_webhook_matches_spec() -> None:
    assert sign_webhook(b"{}", "k", "1") == sign_webhook(b"{}", "k", "1")
    assert sign_webhook(b"{}", "k", "1") != sign_webhook(b"{}", "k", "2")


def test_legacy_endpoints_still_work() -> None:
    client = create_app().test_client()
    assert client.get("/health").get_json()["service"] == "mocks"
    assert client.post("/mocks/bright-data/snapshot", json={}).get_json()["status"] == "ready"
    assert client.post("/mocks/telegram", json={"a": 1}).get_json()["ok"] is True
