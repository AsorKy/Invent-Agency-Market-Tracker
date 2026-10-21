"""Unit tests for the resilience gaps of T-124 (REQ-OBS-001, REQ-EVT-007).

Covers 503 on Pulse database errors, the request id carried in stream entries and bound by
consumers, and the shutdown hooks of the stream workers. No database or Redis is used.
"""

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")
testclient = pytest.importorskip("fastapi.testclient")

import structlog  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from libs.common.lifecycle import (  # noqa: E402
    drain_and_run_hooks,
    instrument_app,
    register_shutdown_hook,
)
from services.core.app.dead_letter import stream_fields  # noqa: E402
from services.ml.app import consumer as ml_consumer  # noqa: E402
from services.pulse.app import consumer as pulse_consumer  # noqa: E402
from services.pulse.app import main as pulse  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

HEADERS = service_headers()
ENDPOINTS = [
    "/internal/pulse/unprocessed-signals",
    "/internal/pulse/pipeline-backlog",
    "/internal/pulse/source-efficiency",
    "/internal/pulse/time-in-pipeline",
    "/internal/pulse/top-sources",
    "/internal/pulse/ops/health",
]


class BrokenRepository:
    """Every read fails like a database that is down."""

    def __getattr__(self, name: str) -> Any:
        async def fail(**_: Any) -> None:
            raise OperationalError("select", {}, ConnectionRefusedError("down"))

        return fail


@pytest.mark.parametrize("path", ENDPOINTS)
def test_pulse_endpoints_return_503_on_database_error(path: str) -> None:
    pulse.app.state.repository = BrokenRepository()
    client = testclient.TestClient(pulse.app, raise_server_exceptions=False)
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "database_unavailable"}


def _row(payload: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "event_id": "e1",
        "tenant_id": "t1",
        "event_type": "signal.created",
        "aggregate_type": "signal",
        "aggregate_id": "a1",
        "version": 1,
        "payload": payload,
        **extra,
    }


def test_stream_fields_carry_request_id() -> None:
    assert stream_fields(_row({"request_id": "r-1"}))["request_id"] == "r-1"
    assert stream_fields(_row({}, request_id="r-2"))["request_id"] == "r-2"
    assert "request_id" not in stream_fields(_row({}))


@pytest.mark.parametrize("module", [pulse_consumer, ml_consumer])
def test_consumers_bind_request_id(module: Any) -> None:
    module.bind_request_id({"request_id": "r-9"})
    assert structlog.contextvars.get_contextvars()["request_id"] == "r-9"
    module.bind_request_id({"payload": json.dumps({"request_id": "r-8"})})
    assert structlog.contextvars.get_contextvars()["request_id"] == "r-8"
    module.bind_request_id({"payload": "not json"})
    assert "request_id" not in structlog.contextvars.get_contextvars()


class FakeRedis:
    def __init__(self) -> None:
        self.acked: list[str] = []

    async def xack(self, _stream: str, _group: str, *ids: str) -> None:
        self.acked.extend(ids)


@pytest.mark.parametrize("module", [pulse_consumer, ml_consumer])
def test_shutdown_hook_acknowledges_the_committed_batch(module: Any) -> None:
    async def run() -> FakeRedis:
        redis = FakeRedis()
        shutdown = module.BatchShutdown(redis, "invent.events", "g")
        shutdown.unacked.append("1-0")
        app = FastAPI()
        state = instrument_app(app, "worker")
        register_shutdown_hook(app, shutdown.hook)
        await drain_and_run_hooks(state, timeout=0.1)
        assert shutdown.stop.is_set()
        return redis

    assert asyncio.run(run()).acked == ["1-0"]
