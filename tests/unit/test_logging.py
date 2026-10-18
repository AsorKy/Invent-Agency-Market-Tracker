"""Test one-line JSON logs that carry request_id (REQ-OBS-001)."""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("structlog")
pytest.importorskip("pydantic_settings")
import structlog  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from libs.common.lifecycle import instrument_app  # noqa: E402
from libs.common.logging import configure_logging  # noqa: E402
from libs.common.request_id import RequestIdMiddleware  # noqa: E402


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    instrument_app(app, "t")

    @app.get("/log")
    async def log_route() -> dict[str, str]:
        structlog.get_logger("test").info("hello", detail="x")
        return {"ok": "1"}

    return app


def _json_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("{")]
    assert all("\n" not in line for line in lines)
    return [json.loads(line) for line in lines]


def test_log_is_one_line_json_with_request_id(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    configure_logging("INFO")
    response = TestClient(_app()).get("/log", headers={"X-Request-ID": "req-123"})
    assert response.headers["X-Request-ID"] == "req-123"
    records = [r for r in _json_lines(caplog) if r.get("event") == "hello"]
    assert len(records) == 1
    assert records[0]["request_id"] == "req-123"
    assert records[0]["level"] == "info"


def test_generated_request_id_matches_response_header(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    configure_logging("INFO")
    response = TestClient(_app()).get("/log")
    records = [r for r in _json_lines(caplog) if r.get("event") == "hello"]
    assert records[0]["request_id"] == response.headers["X-Request-ID"]


def test_request_id_not_leaked_after_request(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    configure_logging("INFO")
    TestClient(_app()).get("/log", headers={"X-Request-ID": "req-9"})
    caplog.clear()
    structlog.get_logger("test").info("after")
    records = [r for r in _json_lines(caplog) if r.get("event") == "after"]
    assert "request_id" not in records[0]
