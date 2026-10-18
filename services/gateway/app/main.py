"""Route and protect the public ``/api/v1`` contract.

``_route_for`` translates public resources into internal paths and ``proxy``
forwards headers, query parameters, and body with the service token. The
gateway also applies configuration-gated edge and CSRF checks, while identity
validation remains an explicit hook for the future Identity service. It
relates to ``libs.common.settings``, ``libs.common.security``,
``libs.common.log_redaction``, and the API services in ``docker-compose.yml``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from redis.exceptions import RedisError

from libs.common.log_redaction import redact_headers, redact_text
from libs.common.logging import configure_logging
from libs.common.rate_limiter import RedisSlidingWindowRateLimiter
from libs.common.redis_client import check_redis, create_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.retry import retry_with_backoff
from libs.common.security import (
    GatewaySecurityConfig,
    IdentityValidationNotConfigured,
    csrf_is_required,
    valid_double_submit_csrf,
    valid_edge_secret,
    validate_identity_if_enabled,
)
from libs.common.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger(__name__)
security_config = GatewaySecurityConfig.from_settings(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis = create_redis(settings.redis_url)
    app.state.rate_limiter = RedisSlidingWindowRateLimiter(
        redis,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    yield
    await redis.aclose()


app = FastAPI(
    title="Invent Market Tracker Gateway",
    version=settings.app_version,
    lifespan=lifespan,
)
app.add_middleware(RequestIdMiddleware)


def _route_for(path: str) -> tuple[str, str] | None:
    first, _, suffix = path.partition("/")
    if first == "pulse":
        service = "pulse"
        base_url = settings.pulse_url
        internal_path = f"/internal/{service}/{suffix}" if suffix else f"/internal/{service}"
        return base_url.rstrip("/") + internal_path, service
    if first == "brief":
        base_url = settings.brief_url
        internal_path = f"/internal/brief/{suffix}" if suffix else "/internal/brief"
        return base_url.rstrip("/") + internal_path, "brief"
    if first in {"auth", "me", "logout"}:
        internal_path = f"/internal/identity/{path}"
        return settings.identity_url.rstrip("/") + internal_path, "identity"
    if first == "imports":
        internal_path = f"/internal/core/imports/{suffix}" if suffix else "/internal/core/imports"
        return settings.core_url.rstrip("/") + internal_path, "core"
    if first == "jobs":
        internal_path = f"/internal/core/jobs/{suffix}" if suffix else "/internal/core/jobs"
        return settings.core_url.rstrip("/") + internal_path, "core"
    if first == "leads" and suffix.split("/")[-1:] == ["brief"]:
        lead_path = suffix.rsplit("/", 1)[0]
        base_url = settings.brief_url
        internal_path = f"/internal/brief/leads/{lead_path}"
        return base_url.rstrip("/") + internal_path, "brief"
    routes = {
        "ingest": (settings.ingest_url, "ingest"),
        "companies": (settings.core_url, "core"),
        "leads": (settings.core_url, "core"),
        "pipeline": (settings.core_url, "core"),
        "projects": (settings.core_url, "core"),
        "signals": (settings.core_url, "core"),
        "market": (settings.analytics_url, "analytics"),
        "segments": (settings.analytics_url, "analytics"),
        "territory": (settings.analytics_url, "analytics"),
        "ops": (settings.analytics_url, "analytics"),
        "scores": (settings.ml_url, "ml"),
    }
    route = routes.get(first)
    if route is None:
        return None
    base_url, service = route
    if service == "ingest":
        internal_path = f"/internal/ingest/{suffix}" if suffix else "/internal/ingest"
    else:
        internal_path = f"/internal/{service}/{first}"
    if suffix:
        if service != "ingest":
            internal_path += f"/{suffix}"
    return base_url.rstrip("/") + internal_path, service


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gateway", "version": settings.app_version}


@app.get("/ready")
async def ready() -> JSONResponse:
    upstreams = {
        "ingest": settings.ingest_url,
        "core": settings.core_url,
        "analytics": settings.analytics_url,
        "identity": settings.identity_url,
        "pulse": settings.pulse_url,
        "brief": settings.brief_url,
        "ml": settings.ml_url,
    }
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
    route = _route_for(path)
    if route is None:
        return JSONResponse(status_code=404, content={"detail": "route_not_found"})
    csrf_required = csrf_is_required(request.method, request.url.path, security_config)
    if csrf_required and not valid_double_submit_csrf(request.cookies, request.headers):
        logger.debug(
            "gateway_request_rejected",
            reason="csrf_token_invalid",
            headers=redact_headers(request.headers),
        )
        return JSONResponse(status_code=403, content={"detail": "csrf_token_invalid"})
    try:
        await validate_identity_if_enabled(
            request,
            getattr(request.app.state, "identity_validator", None),
            security_config,
        )
    except IdentityValidationNotConfigured:
        return JSONResponse(
            status_code=503,
            content={"detail": "identity_validation_not_configured"},
        )
    target, _service = route
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
    body = await request.body()
    request_id = getattr(request.state, "request_id", None)
    if not isinstance(request_id, str):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
    upstream_headers: dict[str, str] = {
        "X-Request-ID": request_id,
        "X-Service-Token": settings.internal_service_token,
    }
    for header_name in (
        "content-type",
        "cookie",
        "authorization",
        "x-csrf-token",
        "x-brightdata-signature",
        "x-brightdata-timestamp",
    ):
        header_value = request.headers.get(header_name)
        if header_value:
            upstream_headers[header_name] = header_value
    async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as client:
        try:

            async def operation() -> httpx.Response:
                return await client.request(
                    request.method,
                    target,
                    params=request.query_params,
                    content=body,
                    headers=upstream_headers,
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
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower()
        not in {"content-length", "transfer-encoding", "connection", "content-encoding"}
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )
