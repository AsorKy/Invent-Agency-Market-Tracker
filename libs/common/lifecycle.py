"""Shared graceful shutdown, request metrics, and request-id log binding.

``instrument_app`` is called by every ``services/*/app/main.py``. It installs one
ASGI middleware that counts in-flight and finished requests, binds ``request_id``
into the structlog context for the duration of each request, and rejects new
requests with 503 once shutdown starts. It also serves Prometheus text metrics at
``/internal/metrics`` behind the internal service token; the gateway never routes
that path. ``graceful_lifespan`` wraps a service lifespan so that on shutdown
(REQ-EVT-007) the app stops accepting, waits up to ``SHUTDOWN_GRACE_SECONDS`` for
active requests, runs registered hooks (flush the outbox relay, acknowledge the
current consumer batch) and only then lets the wrapped lifespan close its pools.
Metrics are in-process and dependency free; they follow REQ-OBS-001.
"""

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import structlog
from fastapi import Depends, FastAPI
from fastapi.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from libs.common.internal_auth import require_internal_token

DEFAULT_GRACE_SECONDS = 25.0
OPEN_WHILE_DRAINING = frozenset({"/health", "/internal/metrics"})
REQUEST_ID_HEADER = b"x-request-id"
ShutdownHook = Callable[[], Awaitable[None]]
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]

logger = structlog.get_logger("lifecycle")


def grace_seconds() -> float:
    """Return the drain wait; ``SHUTDOWN_GRACE_SECONDS`` overrides the 25 s default."""

    raw = os.environ.get("SHUTDOWN_GRACE_SECONDS")
    try:
        return float(raw) if raw is not None else DEFAULT_GRACE_SECONDS
    except ValueError:
        return DEFAULT_GRACE_SECONDS


class ServiceState:
    """Mutable per-app counters and shutdown flag."""

    def __init__(self, service: str) -> None:
        self.service = service
        self.in_flight = 0
        self.draining = False
        self.requests: dict[tuple[str, str, str], int] = {}
        self.duration_sum = 0.0
        self.duration_count = 0
        self.hooks: list[ShutdownHook] = []
        self.idle = asyncio.Event()
        self.idle.set()

    def render(self) -> str:
        name = self.service
        lines = [
            "# TYPE http_requests_total counter",
        ]
        for (method, route, status), count in sorted(self.requests.items()):
            lines.append(
                f'http_requests_total{{service="{name}",method="{method}",'
                f'route="{route}",status="{status}"}} {count}'
            )
        lines += [
            "# TYPE http_requests_in_flight gauge",
            f'http_requests_in_flight{{service="{name}"}} {self.in_flight}',
            "# TYPE http_request_duration_seconds summary",
            f'http_request_duration_seconds_sum{{service="{name}"}} {self.duration_sum:.6f}',
            f'http_request_duration_seconds_count{{service="{name}"}} {self.duration_count}',
            "# TYPE service_draining gauge",
            f'service_draining{{service="{name}"}} {int(self.draining)}',
        ]
        return "\n".join(lines) + "\n"


class _InstrumentMiddleware:
    def __init__(self, app: ASGIApp, state: ServiceState) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = self.state
        headers = list(scope.get("headers", []))
        request_id = next((v.decode("latin-1") for k, v in headers if k == REQUEST_ID_HEADER), "")
        if not request_id:
            request_id = str(uuid.uuid4())
            headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
            scope["headers"] = headers
        structlog.contextvars.bind_contextvars(request_id=request_id)
        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        started = time.perf_counter()
        state.in_flight += 1
        state.idle.clear()
        try:
            if state.draining and scope.get("path") not in OPEN_WHILE_DRAINING:
                status["code"] = 503
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [(b"content-type", b"application/json"), (b"retry-after", b"1")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"detail":"shutting_down"}',
                    }
                )
                return
            await self.app(scope, receive, send_wrapper)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            key = (scope.get("method", ""), route, str(status["code"]))
            state.requests[key] = state.requests.get(key, 0) + 1
            state.duration_sum += time.perf_counter() - started
            state.duration_count += 1
            state.in_flight -= 1
            if state.in_flight == 0:
                state.idle.set()
            structlog.contextvars.unbind_contextvars("request_id")


def instrument_app(app: FastAPI, service: str) -> ServiceState:
    """Install metrics, request-id log binding, drain gate, and ``/internal/metrics``."""

    state = ServiceState(service)
    app.state.service_state = state
    app.add_middleware(_InstrumentMiddleware, state=state)

    @app.get("/internal/metrics", dependencies=[Depends(require_internal_token)])
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(state.render(), media_type="text/plain; version=0.0.4")

    return state


def register_shutdown_hook(app: FastAPI, hook: ShutdownHook) -> None:
    """Register work (flush relay, ack batch) to run after draining, before pools close."""

    app.state.service_state.hooks.append(hook)


async def drain_and_run_hooks(state: ServiceState, timeout: float | None = None) -> bool:
    """Stop accepting, wait for active requests, then run hooks; return True if drained."""

    state.draining = True
    wait = grace_seconds() if timeout is None else timeout
    drained = True
    try:
        await asyncio.wait_for(state.idle.wait(), timeout=wait)
    except TimeoutError:
        drained = False
        logger.warning("shutdown_drain_timeout", in_flight=state.in_flight)
    for hook in state.hooks:
        try:
            await hook()
        except Exception:
            logger.exception("shutdown_hook_failed")
    return drained


def graceful_lifespan(inner: Lifespan | None = None) -> Lifespan:
    """Wrap ``inner`` so shutdown drains and runs hooks before ``inner`` closes pools."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = getattr(app.state, "service_state", None)
        if state is not None:
            state.draining = False
        if inner is None:
            yield
            await _drain(app)
            return
        async with inner(app):
            yield
            await _drain(app)

    return lifespan


async def _drain(app: FastAPI) -> None:
    state: Any = getattr(app.state, "service_state", None)
    if state is not None:
        await drain_and_run_hooks(state)
