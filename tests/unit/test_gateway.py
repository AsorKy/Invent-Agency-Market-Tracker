"""Unit tests for the public gateway in ``services/gateway/app/main.py``.

Covers REQ-QA-002 (offline unit coverage), REQ-GW-001 (health, readiness, routing,
edge secret, CSRF) and REQ-GW-002 (rate limiting and request-id propagation). The
app runs in-process through the ASGI client; upstream services are replaced by an
``httpx.MockTransport`` handler and Redis by fakeredis or a stub limiter, so no
network, Redis, or Postgres is used. The module skips when dependencies are absent.
"""

import dataclasses
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
pytest.importorskip("fakeredis")
pytest.importorskip("structlog")
redis_exceptions = pytest.importorskip("redis.exceptions")

from libs.common.rate_limiter import (  # noqa: E402
    RateLimitDecision,
    RedisSlidingWindowRateLimiter,
)
from services.gateway.app import main as gw  # noqa: E402

REAL_ASYNC_CLIENT = httpx.AsyncClient
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CSRF_COOKIE = "csrf"  # local profile name; production uses "__Host-csrf"


class Upstream:
    """Record requests sent to fake upstream services and answer with a canned reply."""

    def __init__(self, status: int = 200, fail: bool = False) -> None:
        self.status = status
        self.fail = fail
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("upstream down", request=request)
        return httpx.Response(
            self.status,
            json={"ok": True},
            headers={"X-Upstream": "yes", "Connection": "close"},
        )


class StubLimiter:
    """Return scripted rate-limit decisions or raise, and record identities."""

    def __init__(self, decision: Any = None, error: Exception | None = None) -> None:
        self.decision = decision or RateLimitDecision(allowed=True, limit=5, remaining=4)
        self.error = error
        self.identities: list[str] = []

    async def check(self, identity: str) -> Any:
        self.identities.append(identity)
        if self.error is not None:
            raise self.error
        return self.decision


def install_upstream(monkeypatch: pytest.MonkeyPatch, handler: Callable[[Any], Any]) -> None:
    """Make the gateway's httpx client talk to ``handler`` instead of the network."""

    def factory(*args: Any, **kwargs: Any) -> Any:
        return REAL_ASYNC_CLIENT(*args, transport=httpx.MockTransport(handler), **kwargs)

    shim = SimpleNamespace(
        AsyncClient=factory,
        HTTPError=httpx.HTTPError,
        TransportError=httpx.TransportError,
        Response=httpx.Response,
    )
    monkeypatch.setattr(gw, "httpx", shim)


def override_settings(monkeypatch: pytest.MonkeyPatch, **changes: Any) -> None:
    monkeypatch.setattr(gw, "settings", gw.settings.model_copy(update=changes))


def override_security(monkeypatch: pytest.MonkeyPatch, **changes: Any) -> None:
    monkeypatch.setattr(gw, "security_config", dataclasses.replace(gw.security_config, **changes))


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> Upstream:
    fake = Upstream()
    install_upstream(monkeypatch, fake)
    override_settings(
        monkeypatch,
        upstream_retry_base_delay_seconds=0,
        upstream_retry_max_delay_seconds=0,
        upstream_retry_attempts=2,
    )
    return fake


@pytest.fixture
async def client(asgi_client: Any, upstream: Upstream) -> Any:
    """Gateway client with a permissive stub limiter installed after the lifespan starts."""
    async with asgi_client(gw.app) as http:
        gw.app.state.rate_limiter = StubLimiter()
        yield http


# --- health and readiness ----------------------------------------------------


async def test_health_reports_ok_and_is_not_proxied(client: Any, upstream: Upstream) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "gateway"
    assert upstream.requests == []


async def test_ready_ok_when_redis_and_all_upstreams_respond(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def redis_up(_url: str) -> bool:
        return True

    monkeypatch.setattr(gw, "check_redis", redis_up)

    response = await client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert set(body["dependencies"]) == {
        "redis",
        "ingest",
        "core",
        "identity",
        "pulse",
        "brief",
        "ml",
    }
    assert all(body["dependencies"].values())
    assert {request.url.path for request in upstream.requests} == {"/ready"}


async def test_ready_is_503_when_one_upstream_is_unreachable(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def redis_up(_url: str) -> bool:
        return True

    ml_port = httpx.URL(gw.settings.ml_url).port

    def handler(request: Any) -> Any:
        if request.url.port == ml_port:
            raise httpx.ConnectError("ml down", request=request)
        return httpx.Response(200, json={"status": "ready"})

    monkeypatch.setattr(gw, "check_redis", redis_up)
    install_upstream(monkeypatch, handler)

    response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["ml"] is False
    assert body["dependencies"]["core"] is True
    assert body["dependencies"]["redis"] is True


async def test_ready_is_503_when_upstream_answers_with_error_status(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def redis_up(_url: str) -> bool:
        return True

    monkeypatch.setattr(gw, "check_redis", redis_up)
    install_upstream(monkeypatch, lambda request: httpx.Response(503))

    response = await client.get("/ready")

    assert response.status_code == 503
    assert not any(
        value for name, value in response.json()["dependencies"].items() if name != "redis"
    )


async def test_ready_is_503_when_redis_is_down(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def redis_down(_url: str) -> bool:
        return False

    monkeypatch.setattr(gw, "check_redis", redis_down)

    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["dependencies"]["redis"] is False


# --- routing and 404 ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/unknown",
        "/api/v1/internal/core/companies",
        "/api/v1/",
        "/internal/core/companies",
        "/metrics",
    ],
)
async def test_non_public_paths_return_404_without_calling_upstream(
    client: Any, upstream: Upstream, path: str
) -> None:
    response = await client.get(path)

    assert response.status_code == 404
    assert upstream.requests == []
    if path.startswith("/api/v1"):
        assert response.json() == {"detail": "route_not_found"}


async def test_public_resource_is_translated_to_internal_path_with_query(
    client: Any, upstream: Upstream
) -> None:
    response = await client.get("/api/v1/companies/42?limit=5&q=cafe")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    sent = upstream.requests[0]
    assert sent.method == "GET"
    assert sent.url.path == "/internal/core/companies/42"
    assert dict(sent.url.params) == {"limit": "5", "q": "cafe"}
    assert sent.headers["X-Service-Token"] == gw.settings.internal_service_token


@pytest.mark.parametrize(
    ("public", "internal", "port_attr"),
    [
        ("/api/v1/ingest/runs", "/internal/ingest/runs", "ingest_url"),
        ("/api/v1/market/summary", "/internal/pulse/market/summary", "pulse_url"),
        ("/api/v1/scores/7", "/internal/ml/scores/7", "ml_url"),
        ("/api/v1/me", "/internal/identity/me", "identity_url"),
        ("/api/v1/imports/3", "/internal/core/imports/3", "core_url"),
        ("/api/v1/leads/9/brief", "/internal/brief/leads/9", "brief_url"),
        ("/api/v1/leads/9", "/internal/core/leads/9", "core_url"),
    ],
)
async def test_routes_reach_the_owning_service(
    client: Any, upstream: Upstream, public: str, internal: str, port_attr: str
) -> None:
    response = await client.get(public)

    assert response.status_code == 200
    sent = upstream.requests[0]
    assert sent.url.path == internal
    assert sent.url.port == httpx.URL(getattr(gw.settings, port_attr)).port


async def test_hop_by_hop_upstream_headers_are_not_copied_to_response(
    client: Any, upstream: Upstream
) -> None:
    response = await client.get("/api/v1/companies")

    assert response.headers["X-Upstream"] == "yes"
    assert response.headers.get("connection") != "close"


async def test_upstream_status_and_body_pass_through(client: Any, upstream: Upstream) -> None:
    upstream.status = 422

    response = await client.get("/api/v1/companies")

    assert response.status_code == 422
    assert response.json() == {"ok": True}


async def test_unreachable_upstream_returns_503_and_get_is_retried(
    client: Any, upstream: Upstream
) -> None:
    upstream.fail = True

    response = await client.get("/api/v1/companies")

    assert response.status_code == 503
    assert response.json() == {"detail": "upstream_unavailable"}
    assert len(upstream.requests) == gw.settings.upstream_retry_attempts


async def test_post_is_not_retried_and_forwards_body(client: Any, upstream: Upstream) -> None:
    upstream.fail = True

    response = await client.post("/api/v1/companies", content=b'{"name":"x"}')

    assert response.status_code == 503
    assert len(upstream.requests) == 1
    assert upstream.requests[0].content == b'{"name":"x"}'


# --- edge secret -------------------------------------------------------------


async def test_edge_secret_missing_or_wrong_returns_404_never_403(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_security(monkeypatch, edge_secret="edge-s3cret", edge_secret_required=True)

    missing = await client.get("/api/v1/companies")
    wrong = await client.get("/api/v1/companies", headers={"X-Edge-Secret": "nope"})
    unknown_route = await client.get("/api/v1/unknown")

    for response in (missing, wrong, unknown_route):
        assert response.status_code == 404
        assert response.json() == {"detail": "route_not_found"}
    assert upstream.requests == []


async def test_edge_secret_rejection_precedes_csrf_check(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_security(
        monkeypatch, edge_secret="edge-s3cret", edge_secret_required=True, csrf_enabled=True
    )

    response = await client.post("/api/v1/companies", content=b"{}")

    assert response.status_code == 404
    assert upstream.requests == []


async def test_edge_secret_valid_allows_request(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_security(monkeypatch, edge_secret="edge-s3cret", edge_secret_required=True)

    response = await client.get("/api/v1/companies", headers={"X-Edge-Secret": "edge-s3cret"})

    assert response.status_code == 200
    assert len(upstream.requests) == 1
    assert "x-edge-secret" not in upstream.requests[0].headers


async def test_health_is_reachable_without_edge_secret(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_security(monkeypatch, edge_secret="edge-s3cret", edge_secret_required=True)

    response = await client.get("/health")

    assert response.status_code == 200


# --- CSRF --------------------------------------------------------------------


async def test_csrf_missing_token_on_unsafe_method_is_403(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_security(monkeypatch, csrf_enabled=True)

    response = await client.post("/api/v1/companies", content=b"{}")

    assert response.status_code == 403
    assert response.json() == {"detail": "csrf_token_invalid"}
    assert upstream.requests == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Cookie": f"{CSRF_COOKIE}=abc"},
        {"X-CSRF-Token": "abc"},
        {"Cookie": f"{CSRF_COOKIE}=abc", "X-CSRF-Token": "different"},
    ],
)
async def test_csrf_incomplete_or_mismatched_pair_is_403(
    client: Any,
    upstream: Upstream,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    override_security(monkeypatch, csrf_enabled=True)

    response = await client.post("/api/v1/companies", content=b"{}", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "csrf_token_invalid"}
    assert upstream.requests == []


async def test_csrf_matching_double_submit_is_forwarded(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_security(monkeypatch, csrf_enabled=True)

    response = await client.post(
        "/api/v1/companies",
        content=b"{}",
        headers={"Cookie": f"{CSRF_COOKIE}=tok123", "X-CSRF-Token": "tok123"},
    )

    assert response.status_code == 200
    sent = upstream.requests[0]
    assert "Cookie" not in sent.headers  # session cookies reach identity only


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_csrf_applies_to_every_unsafe_method(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    override_security(monkeypatch, csrf_enabled=True)

    response = await client.request(method, "/api/v1/companies/1")

    assert response.status_code == 403
    assert upstream.requests == []


async def test_csrf_not_required_for_safe_methods(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_security(monkeypatch, csrf_enabled=True)

    response = await client.get("/api/v1/companies")

    assert response.status_code == 200


async def test_csrf_bootstrap_login_path_is_exempt(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_security(monkeypatch, csrf_enabled=True)

    response = await client.post("/api/v1/auth/session", content=b"{}")

    assert response.status_code == 200
    assert upstream.requests[0].url.path == "/internal/identity/auth/session"


async def test_csrf_disabled_by_default_allows_unsafe_method(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_security(monkeypatch, csrf_enabled=False)

    response = await client.post("/api/v1/companies", content=b"{}")

    assert response.status_code == 200


async def test_identity_validation_enabled_requires_credentials(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_security(monkeypatch, identity_validation_enabled=True)

    response = await client.get("/api/v1/companies")

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication_required"}
    assert upstream.requests == []


# --- rate limiting -----------------------------------------------------------


async def test_rate_limiter_with_fake_redis_blocks_after_limit(
    asgi_client: Any, fake_redis: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("lupa")  # fakeredis needs lupa to execute the Lua script
    async with asgi_client(gw.app) as http:
        gw.app.state.rate_limiter = RedisSlidingWindowRateLimiter(
            fake_redis, limit=2, window_seconds=60
        )

        first = await http.get("/api/v1/companies")
        second = await http.get("/api/v1/companies")
        third = await http.get("/api/v1/companies")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json() == {"detail": "rate_limit_exceeded"}
    assert int(third.headers["Retry-After"]) >= 1
    assert third.headers["X-RateLimit-Limit"] == "2"
    assert third.headers["X-RateLimit-Remaining"] == "0"
    assert len(upstream.requests) == 2


async def test_rate_limited_request_is_not_forwarded(client: Any, upstream: Upstream) -> None:
    gw.app.state.rate_limiter = StubLimiter(
        RateLimitDecision(allowed=False, limit=5, remaining=0, retry_after_seconds=17)
    )

    response = await client.get("/api/v1/companies")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "17"
    assert response.headers["X-RateLimit-Limit"] == "5"
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert upstream.requests == []


async def test_rate_limit_identity_ignores_forwarded_for_unless_trusted(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    limiter = StubLimiter()
    gw.app.state.rate_limiter = limiter
    override_settings(monkeypatch, trust_proxy_headers=False)

    await client.get("/api/v1/companies", headers={"X-Forwarded-For": "203.0.113.9"})

    assert limiter.identities == ["127.0.0.1"]


async def test_rate_limit_identity_uses_first_forwarded_for_when_trusted(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    limiter = StubLimiter()
    gw.app.state.rate_limiter = limiter
    override_settings(monkeypatch, trust_proxy_headers=True)

    await client.get("/api/v1/companies", headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})

    assert limiter.identities == ["203.0.113.9"]


async def test_redis_failure_fails_open_by_default(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw.app.state.rate_limiter = StubLimiter(error=redis_exceptions.ConnectionError("down"))
    override_settings(monkeypatch, rate_limit_fail_open=True)

    response = await client.get("/api/v1/companies")

    assert response.status_code == 200
    assert len(upstream.requests) == 1


async def test_redis_failure_fails_closed_when_configured(
    client: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw.app.state.rate_limiter = StubLimiter(error=redis_exceptions.ConnectionError("down"))
    override_settings(monkeypatch, rate_limit_fail_open=False)

    response = await client.get("/api/v1/companies")

    assert response.status_code == 503
    assert response.json() == {"detail": "rate_limiter_unavailable"}
    assert upstream.requests == []


# --- request id --------------------------------------------------------------


async def test_supplied_request_id_is_echoed_and_forwarded(client: Any, upstream: Upstream) -> None:
    response = await client.get("/api/v1/companies", headers={"X-Request-ID": "req-abc-123"})

    assert response.headers["X-Request-ID"] == "req-abc-123"
    assert upstream.requests[0].headers["X-Request-ID"] == "req-abc-123"


async def test_missing_request_id_is_generated_and_matches_upstream(
    client: Any, upstream: Upstream
) -> None:
    response = await client.get("/api/v1/companies")

    generated = response.headers["X-Request-ID"]
    assert UUID_RE.match(generated)
    assert upstream.requests[0].headers["X-Request-ID"] == generated


async def test_request_id_is_present_on_health_and_error_responses(
    client: Any, upstream: Upstream
) -> None:
    health = await client.get("/health", headers={"X-Request-ID": "rid-1"})
    missing = await client.get("/api/v1/unknown")
    upstream.fail = True
    failed = await client.get("/api/v1/companies", headers={"X-Request-ID": "rid-2"})

    assert health.headers["X-Request-ID"] == "rid-1"
    assert UUID_RE.match(missing.headers["X-Request-ID"])
    assert failed.status_code == 503
    assert failed.headers["X-Request-ID"] == "rid-2"
