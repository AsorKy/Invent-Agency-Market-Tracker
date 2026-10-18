"""Test graceful shutdown and the metrics endpoint (REQ-EVT-007, REQ-OBS-001)."""

import asyncio
import importlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("structlog")
pytest.importorskip("pydantic_settings")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from libs.common.internal_auth import sign_service_token  # noqa: E402
from libs.common.lifecycle import (  # noqa: E402
    graceful_lifespan,
    instrument_app,
    register_shutdown_hook,
)
from libs.common.settings import get_settings  # noqa: E402

SERVICES = ["core", "gateway", "identity", "brief", "ingest", "ml", "pulse", "streamlit"]


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "lifespan-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("service", SERVICES)
def test_metrics_requires_token_and_serves_text(service: str) -> None:
    module = importlib.import_module(f"services.{service}.app.main")
    client = TestClient(module.app)
    assert client.get("/internal/metrics").status_code == 401
    token = sign_service_token("gateway", "lifespan-secret")
    response = client.get("/internal/metrics", headers={"X-Service-Token": token})
    assert response.status_code == 200
    assert f'service="{service}"' in response.text
    assert "http_requests_in_flight" in response.text


def test_shutdown_order_and_closing_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def inner(app: FastAPI):
        yield
        events.append("close_pools")

    app = FastAPI(lifespan=graceful_lifespan(inner))
    instrument_app(app, "t")

    async def flush() -> None:
        events.append("flush_relay")

    async def ack() -> None:
        events.append("ack_batch")

    register_shutdown_hook(app, flush)
    register_shutdown_hook(app, ack)
    with TestClient(app):
        pass
    assert events == ["flush_relay", "ack_batch", "close_pools"]


def test_drain_waits_for_active_request_and_rejects_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from libs.common.lifecycle import drain_and_run_hooks

    app = FastAPI()
    state = instrument_app(app, "t")
    release = asyncio.Event()

    async def scenario() -> tuple[bool, bool]:
        state.in_flight = 1
        state.idle.clear()
        task = asyncio.create_task(drain_and_run_hooks(state, timeout=2))
        await asyncio.sleep(0.05)
        assert state.draining and not task.done()
        release.set()
        state.in_flight = 0
        state.idle.set()
        drained = await task
        state.in_flight = 1
        state.idle.clear()
        timed_out = await drain_and_run_hooks(state, timeout=0.05)
        return drained, timed_out

    drained, second = asyncio.run(scenario())
    assert drained is True
    assert second is False


def test_requests_rejected_while_draining() -> None:
    app = FastAPI()
    state = instrument_app(app, "t")

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    assert client.get("/ping").status_code == 200
    state.draining = True
    assert client.get("/ping").status_code == 503
    token = sign_service_token("gateway", "lifespan-secret")
    metrics = client.get("/internal/metrics", headers={"X-Service-Token": token}).text
    assert 'route="/ping",status="200"} 1' in metrics
    assert "service_draining" in metrics


def test_grace_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from libs.common.lifecycle import grace_seconds

    monkeypatch.delenv("SHUTDOWN_GRACE_SECONDS", raising=False)
    assert grace_seconds() == 25.0
    monkeypatch.setenv("SHUTDOWN_GRACE_SECONDS", "0.5")
    assert grace_seconds() == 0.5
