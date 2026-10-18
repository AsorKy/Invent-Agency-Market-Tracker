"""Simulate Bright Data and Telegram for local pipeline execution.

The Flask app emulates the Bright Data Datasets API (trigger, progress,
snapshot download) with configurable latency and failure rate, and delivers
webhooks signed like the real provider: HMAC-SHA256 over ``<timestamp>.<body>``
in the ``X-BrightData-Signature`` and ``X-BrightData-Timestamp`` headers, as
verified by ``libs/common/security.py``. Records are synthetic and follow the
provider field names in ``libs/domain/source_mappings``. It relates to
``orchestration/n8n``, ``services/ingest/app/main.py``, and the mock connector
in ``services/ingest/app/connectors.py``; it is not a production connector and
never exposes real credentials.

Settings (environment, read by ``create_app``): ``MOCK_LATENCY_MS``,
``MOCK_FAILURE_RATE`` (0 to 1), ``MOCK_READY_AFTER_S`` (progress stays running
until then), ``MOCK_RECORDS`` (records per snapshot), and
``BRIGHT_DATA_WEBHOOK_SECRET``. Clock, sleeper, random source, and webhook
sender are injectable for tests.
"""

import hashlib
import hmac
import os
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from flask import Flask, Response, jsonify, request

WebhookSender = Callable[[str, bytes, dict[str, str]], int]


@dataclass
class _Snapshot:
    snapshot_id: str
    dataset_id: str
    created_at: float
    notify_url: str | None
    records: list[dict[str, Any]] = field(default_factory=list)


def sign_webhook(body: bytes, secret: str, timestamp: str) -> str:
    """Return the hex HMAC-SHA256 of ``<timestamp>.<body>``."""
    message = timestamp.encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def synthetic_records(dataset_id: str, snapshot_id: str, count: int) -> list[dict[str, Any]]:
    """Build deterministic provider-shaped records for a snapshot."""
    jobs = "computrabajo" in dataset_id
    records: list[dict[str, Any]] = []
    for index in range(count):
        key = f"{snapshot_id}-{index}"
        if jobs:
            records.append(
                {
                    "job_id": f"ct-{key}",
                    "title": f"Software Developer {index}",
                    "company": f"Synthetic Company {index}",
                    "city": "Medellin",
                    "salary": "COP 6,000,000",
                    "description": "Synthetic job posting.",
                    "posted_at": "2026-10-01",
                    "url": f"https://co.computrabajo.example/job/{key}",
                }
            )
        else:
            records.append(
                {
                    "place_id": f"place-{key}",
                    "name": f"Synthetic Company {index}",
                    "category": "Restaurant",
                    "address": f"Calle {10 + index} # 5-20, Medellin",
                    "city": "Medellin",
                    "phone_number": f"+57 300 000 {index:04d}",
                    "open_website": f"https://synthetic-{index}.example",
                    "rating": 4.2,
                    "reviews_count": 10 + index,
                    "lat": 6.2442 + index / 1000,
                    "lon": -75.5812,
                }
            )
    return records


def _post_webhook(url: str, body: bytes, headers: dict[str, str]) -> int:
    response = httpx.post(url, content=body, headers=headers, timeout=10.0)
    return response.status_code


def create_app(
    *,
    latency_ms: float | None = None,
    failure_rate: float | None = None,
    ready_after_s: float | None = None,
    records_per_snapshot: int | None = None,
    webhook_secret: str | None = None,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    webhook_sender: WebhookSender = _post_webhook,
) -> Flask:
    """Build the mock app; unset options come from the environment."""
    env = os.environ
    latency = latency_ms if latency_ms is not None else float(env.get("MOCK_LATENCY_MS", "0"))
    failures = (
        failure_rate if failure_rate is not None else float(env.get("MOCK_FAILURE_RATE", "0"))
    )
    ready_after = (
        ready_after_s if ready_after_s is not None else float(env.get("MOCK_READY_AFTER_S", "0"))
    )
    count = (
        records_per_snapshot
        if records_per_snapshot is not None
        else int(env.get("MOCK_RECORDS", "3"))
    )
    secret = (
        webhook_secret if webhook_secret is not None else env.get("BRIGHT_DATA_WEBHOOK_SECRET", "")
    )
    random_source = rng or random.Random()
    snapshots: dict[str, _Snapshot] = {}
    flask_app = Flask(__name__)

    def disturb() -> Response | None:
        """Apply latency, then return a 503 response with the configured probability."""
        if latency > 0:
            sleeper(latency / 1000)
        if failures > 0 and random_source.random() < failures:
            response = jsonify({"error": "mock_provider_failure"})
            response.status_code = 503
            return response
        return None

    def not_found() -> Response:
        response = jsonify({"error": "unknown_snapshot"})
        response.status_code = 404
        return response

    def is_ready(snap: _Snapshot) -> bool:
        return clock() - snap.created_at >= ready_after

    @flask_app.get("/health")
    def health() -> Response:
        return jsonify({"status": "ok", "service": "mocks", "version": "dev"})

    @flask_app.post("/datasets/v3/trigger")
    def trigger() -> Response:
        failed = disturb()
        if failed is not None:
            return failed
        dataset_id = request.args.get("dataset_id", "mock-maps")
        snapshot_id = f"s_{uuid.uuid4().hex[:16]}"
        snapshots[snapshot_id] = _Snapshot(
            snapshot_id=snapshot_id,
            dataset_id=dataset_id,
            created_at=clock(),
            notify_url=request.args.get("notify") or None,
            records=synthetic_records(dataset_id, snapshot_id, count),
        )
        return jsonify({"snapshot_id": snapshot_id})

    @flask_app.get("/datasets/v3/progress/<snapshot_id>")
    def progress(snapshot_id: str) -> Response:
        failed = disturb()
        if failed is not None:
            return failed
        snap = snapshots.get(snapshot_id)
        if snap is None:
            return not_found()
        ready = is_ready(snap)
        return jsonify(
            {
                "snapshot_id": snapshot_id,
                "status": "ready" if ready else "running",
                "records": len(snap.records) if ready else 0,
            }
        )

    @flask_app.get("/datasets/v3/snapshot/<snapshot_id>")
    def snapshot(snapshot_id: str) -> Response:
        failed = disturb()
        if failed is not None:
            return failed
        snap = snapshots.get(snapshot_id)
        if snap is None:
            return not_found()
        if not is_ready(snap):
            response = jsonify({"status": "running", "message": "snapshot is not ready"})
            response.status_code = 202
            return response
        return jsonify(snap.records)

    @flask_app.post("/mocks/bright-data/webhook/<snapshot_id>")
    def deliver_webhook(snapshot_id: str) -> Response:
        """Send the signed ready notification to the stored notify URL."""
        snap = snapshots.get(snapshot_id)
        if snap is None:
            return not_found()
        if not snap.notify_url:
            response = jsonify({"error": "no_notify_url"})
            response.status_code = 409
            return response
        if not is_ready(snap):
            response = jsonify({"error": "snapshot_not_ready"})
            response.status_code = 409
            return response
        body = jsonify(
            {
                "snapshot_id": snap.snapshot_id,
                "dataset_id": snap.dataset_id,
                "status": "ready",
                "fetched_at": datetime.fromtimestamp(clock(), UTC).isoformat(),
                "payload": snap.records,
            }
        ).get_data()
        timestamp = str(int(clock()))
        headers = {"Content-Type": "application/json", "X-BrightData-Timestamp": timestamp}
        if secret:
            headers["X-BrightData-Signature"] = sign_webhook(body, secret, timestamp)
        status = webhook_sender(snap.notify_url, body, headers)
        return jsonify({"delivered": 200 <= status < 300, "status_code": status})

    @flask_app.post("/mocks/bright-data/snapshot")
    def bright_data_snapshot() -> Response:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            body = {}
        return jsonify(
            {
                "snapshot_id": body.get("snapshot_id", "mock-snapshot-001"),
                "dataset_id": body.get("dataset_id", "google-maps-medellin"),
                "status": "ready",
                "fetched_at": datetime.now(UTC).isoformat(),
                "payload": body.get(
                    "payload",
                    {
                        "name": "Synthetic Company Medellin",
                        "city": "Medellin",
                        "source": "mock",
                    },
                ),
            }
        )

    @flask_app.post("/mocks/telegram")
    def telegram() -> Response:
        return jsonify({"ok": True, "received": request.get_json(silent=True) or {}})

    return flask_app


app = create_app()
