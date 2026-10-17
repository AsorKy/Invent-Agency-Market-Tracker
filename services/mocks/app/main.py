"""Simulate Bright Data and Telegram for local pipeline execution.

The Flask handlers return a synthetic snapshot and acknowledge notifications
without leaving Docker. It relates to the workflows in
``orchestration/n8n`` and ``services/ingest/app/main.py``; it is not a
production connector. It validates wiring, payloads, and readiness without
exposing real credentials.
"""

from datetime import UTC, datetime

from flask import Flask, Response, jsonify, request

app = Flask(__name__)


@app.get("/health")
def health() -> Response:
    return jsonify({"status": "ok", "service": "mocks", "version": "dev"})


@app.post("/mocks/bright-data/snapshot")
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


@app.post("/mocks/telegram")
def telegram() -> Response:
    return jsonify({"ok": True, "received": request.get_json(silent=True) or {}})
