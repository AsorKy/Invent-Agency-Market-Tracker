"""Route and protect the public ``/api/v1`` contract.

``_route_for`` translates public resources into internal paths and ``proxy``
forwards headers, query parameters, and body with the service token. The
gateway also applies configuration-gated edge and CSRF checks and, when
identity validation is enabled, verifies user JWTs against the identity JWKS and
sets the tenant and role headers itself. Routes live in ``routes.py``, JWT logic
in ``auth.py``, and upstream readiness polling in ``readiness.py``. It relates to
``libs.common.settings``, ``libs.common.security``,
``libs.common.log_redaction``, and the API services in ``docker-compose.yml``.
"""

import asyncio
import hmac
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from redis.exceptions import RedisError

from libs.common.internal_auth import GATEWAY_SERVICE, service_token_header
from libs.common.lifecycle import graceful_lifespan, instrument_app
from libs.common.log_redaction import redact_headers, redact_text
from libs.common.logging import configure_logging
from libs.common.rate_limiter import RedisSlidingWindowRateLimiter
from libs.common.redis_client import check_redis, create_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.retry import retry_with_backoff
from libs.common.security import (
    CSRF_HEADER_NAME,
    GatewaySecurityConfig,
    csrf_is_required,
    valid_edge_secret,
)
from libs.common.settings import get_settings
from services.gateway.app.auth import (
    AuthError,
    Claims,
    JwksCache,
    identity_headers,
    scope_allows,
    verify_token,
)
from services.gateway.app.readiness import UpstreamReadiness
from services.gateway.app.routes import (
    IDENTITY_PUBLIC_PATHS,
    ROUTE_TIMEOUTS,
    NotInLayer,
    Route,
    resolve,
)

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger(__name__)
security_config = GatewaySecurityConfig.from_settings(settings)


def _upstreams() -> dict[str, str]:
    return {
        "ingest": settings.ingest_url,
        "core": settings.core_url,
        "identity": settings.identity_url,
        "pulse": settings.pulse_url,
        "brief": settings.brief_url,
        "ml": settings.ml_url,
    }


def _client(timeout: float | None = None) -> Any:
    return httpx.AsyncClient(timeout=timeout or settings.readiness_timeout_seconds)


def _http_errors() -> tuple[type[BaseException], ...]:
    return (httpx.HTTPError,)


jwks_cache = JwksCache(
    lambda: settings.identity_url.rstrip("/") + "/.well-known/jwks.json",
    lambda: _client(ROUTE_TIMEOUTS["identity"]),
    _http_errors,
)
readiness = UpstreamReadiness(_upstreams, _client, _http_errors)


def session_cookie_name() -> str:
    """Return the session cookie name of the active profile."""
    return "__Host-session" if settings.app_profile == "production" else "session"


def csrf_cookie_name() -> str:
    """Return the CSRF cookie name of the active profile."""
    return "__Host-csrf" if settings.app_profile == "production" else "csrf"


def valid_csrf(cookies: Mapping[str, str], headers: Mapping[str, str]) -> bool:
    """Compare the profile-specific CSRF cookie with the header (double submit)."""
    cookie_token = cookies.get(csrf_cookie_name())
    header_token = headers.get(CSRF_HEADER_NAME) or headers.get("x-csrf-token")
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis = create_redis(settings.redis_url)
    app.state.rate_limiter = RedisSlidingWindowRateLimiter(
        redis,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    poller = asyncio.create_task(readiness.run())
    try:
        yield
    finally:
        poller.cancel()
        await redis.aclose()


app = FastAPI(
    title="Invent Market Tracker Gateway",
    version=settings.app_version,
    lifespan=graceful_lifespan(lifespan),
)
app.add_middleware(RequestIdMiddleware)
instrument_app(app, "gateway")

_CORS_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_CORS_HEADERS = "Content-Type, X-CSRF-Token, X-Request-ID"


@app.middleware("http")
async def local_cors(request: Request, call_next: Any) -> Response:
    """Allow ``FRONTEND_ORIGIN`` with credentials in the local profile only."""
    origin = request.headers.get("origin")
    allowed = (
        settings.app_profile == "local"
        and origin is not None
        and origin == settings.frontend_origin
    )
    if (
        allowed
        and request.method == "OPTIONS"
        and "access-control-request-method" in request.headers
    ):
        response: Response = Response(status_code=204)
        response.headers["Access-Control-Allow-Methods"] = _CORS_METHODS
        response.headers["Access-Control-Allow-Headers"] = _CORS_HEADERS
        response.headers["Access-Control-Max-Age"] = "600"
    else:
        response = await call_next(request)
    if allowed:
        response.headers["Access-Control-Allow-Origin"] = origin or ""
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gateway", "version": settings.app_version}


@app.get("/ready")
async def ready() -> JSONResponse:
    upstreams = _upstreams()
    result: dict[str, bool] = {"redis": await check_redis(settings.redis_url)}
    async with httpx.AsyncClient(timeout=settings.readiness_timeout_seconds) as client:
        for name, base_url in upstreams.items():
            try:
                response = await client.get(f"{base_url.rstrip('/')}/ready")
                result[name] = response.is_success
            except httpx.HTTPError:
                result[name] = False
    status = 200 if all(result.values()) else 503
    return JSONResponse(
        status_code=status,
        content={"status": "ready" if status == 200 else "degraded", "dependencies": result},
    )


async def _authenticate(request: Request) -> Claims | JSONResponse:
    """Return verified claims from a Bearer token or the session cookie, else a 401/503."""
    unauthenticated = JSONResponse(status_code=401, content={"detail": "authentication_required"})
    token: str | None = None
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    else:
        session = request.cookies.get(session_cookie_name())
        if not session:
            return unauthenticated
        try:
            async with _client(ROUTE_TIMEOUTS["identity"]) as client:
                validated = await client.get(
                    settings.identity_url.rstrip("/") + "/internal/identity/auth/validate",
                    headers={
                        **service_token_header(GATEWAY_SERVICE, settings.internal_service_token),
                        "Cookie": f"{session_cookie_name()}={session}",
                    },
                )
        except httpx.HTTPError:
            return JSONResponse(status_code=503, content={"detail": "upstream_unavailable"})
        if validated.status_code != 200:
            return unauthenticated
        token = validated.json().get("access_token")
    if not token:
        return unauthenticated
    try:
        return await verify_token(token, jwks_cache)
    except AuthError as exc:
        if str(exc) == "jwks_unavailable":
            return JSONResponse(status_code=503, content={"detail": "upstream_unavailable"})
        return unauthenticated


def _authorize(request: Request, route: Route, claims: Claims) -> JSONResponse | None:
    """Apply role and scope rules; cross-scope access looks like a missing resource."""
    if route.admin_only and claims.role != "admin":
        return JSONResponse(status_code=403, content={"detail": "forbidden"})
    params = request.query_params
    if not scope_allows(claims, params.get("city"), params.get("service_line")):
        return JSONResponse(status_code=404, content={"detail": "not_found"})
    return None


@app.api_route(
    "/api/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy(path: str, request: Request) -> Response:
    if not valid_edge_secret(
        request.headers,
        security_config.edge_secret,
        required=security_config.edge_secret_required,
    ):
        logger.debug(
            "gateway_request_rejected",
            reason="invalid_edge_secret",
            headers=redact_headers(request.headers),
        )
        return JSONResponse(status_code=404, content={"detail": "route_not_found"})
    resolved = resolve(path, request.method, settings)
    if isinstance(resolved, NotInLayer):
        return JSONResponse(status_code=404, content={"detail": "not_in_layer"})
    if resolved == "method_not_allowed":
        return JSONResponse(status_code=405, content={"detail": "method_not_allowed"})
    if resolved is None:
        return JSONResponse(status_code=404, content={"detail": "route_not_found"})
    route: Route = resolved
    if csrf_is_required(request.method, request.url.path, security_config) and not valid_csrf(
        request.cookies, request.headers
    ):
        logger.debug(
            "gateway_request_rejected",
            reason="csrf_token_invalid",
            headers=redact_headers(request.headers),
        )
        return JSONResponse(status_code=403, content={"detail": "csrf_token_invalid"})
    target = route.url
    identity = request.client.host if request.client else ""
    if settings.trust_proxy_headers:
        forwarded_identity = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded_identity:
            identity = forwarded_identity
    identity = identity or "unknown-client"
    try:
        decision = await request.app.state.rate_limiter.check(identity)
    except RedisError:
        logger.warning("rate_limiter_unavailable", identity=redact_text(identity))
        if not settings.rate_limit_fail_open:
            return JSONResponse(status_code=503, content={"detail": "rate_limiter_unavailable"})
        decision = None
    if decision is not None and not decision.allowed:
        rate_headers = {
            "Retry-After": str(decision.retry_after_seconds or settings.rate_limit_window_seconds),
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
        }
        return JSONResponse(
            status_code=429,
            content={"detail": "rate_limit_exceeded"},
            headers=rate_headers,
        )
    claims: Claims | None = None
    if security_config.identity_validation_enabled and not (
        route.service == "identity" and route.public_path in IDENTITY_PUBLIC_PATHS
    ):
        authenticated = await _authenticate(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        claims = authenticated
        denied = _authorize(request, route, claims)
        if denied is not None:
            return denied
    if readiness.is_red(route.service):
        return JSONResponse(status_code=503, content={"detail": "upstream_unavailable"})
    body = await request.body()
    request_id = getattr(request.state, "request_id", None)
    if not isinstance(request_id, str):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
    upstream_headers: dict[str, str] = {"X-Request-ID": request_id}
    forwarded = ["content-type", "x-brightdata-signature", "x-brightdata-timestamp"]
    if route.service == "identity":
        forwarded += ["cookie", "x-csrf-token"]
    for header_name in forwarded:
        header_value = request.headers.get(header_name)
        if header_value:
            upstream_headers[header_name] = header_value
    if claims is not None:
        # Tenant and role headers come only from the verified token, never the client.
        upstream_headers.update(identity_headers(claims))
    timeout = ROUTE_TIMEOUTS.get(route.timeout_key, settings.upstream_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:

            async def operation() -> httpx.Response:
                return await client.request(
                    request.method,
                    target,
                    params=request.query_params,
                    content=body,
                    headers={
                        **upstream_headers,
                        **service_token_header(GATEWAY_SERVICE, settings.internal_service_token),
                    },
                )

            if request.method in {"GET", "PUT", "DELETE"}:
                upstream = await retry_with_backoff(
                    operation,
                    attempts=settings.upstream_retry_attempts,
                    base_delay_seconds=settings.upstream_retry_base_delay_seconds,
                    max_delay_seconds=settings.upstream_retry_max_delay_seconds,
                    jitter_ratio=settings.upstream_retry_jitter_ratio,
                    retry_on=(httpx.TransportError,),
                )
            else:
                upstream = await operation()
        except httpx.HTTPError:
            return JSONResponse(status_code=503, content={"detail": "upstream_unavailable"})
    skipped = {
        "content-length",
        "transfer-encoding",
        "connection",
        "content-encoding",
        "set-cookie",
    }
    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in skipped}
    proxied = Response(
        content=upstream.content, status_code=upstream.status_code, headers=response_headers
    )
    for cookie in upstream.headers.get_list("set-cookie"):
        proxied.headers.append("set-cookie", cookie)
    return proxied
