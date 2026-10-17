"""Route the public ``/api/v1`` contract to internal services.

``_route_for`` translates public resources into internal paths and ``proxy``
forwards headers, query parameters, and body with the service token;
``/ready`` checks every upstream. It relates to ``libs.common.settings``,
``RequestIdMiddleware``, and the five API services in ``docker-compose.yml``.
Its purpose is to provide one entry point for the future frontend.
"""

from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from libs.common.logging import configure_logging
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(title="Invent Market Tracker Gateway", version=settings.app_version)
app.add_middleware(RequestIdMiddleware)


def _route_for(path: str) -> tuple[str, str] | None:
    first, _, suffix = path.partition("/")
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
        "ml": settings.ml_url,
    }
    result: dict[str, bool] = {}
    async with httpx.AsyncClient(timeout=2) as client:
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


@app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request) -> Response:
    route = _route_for(path)
    if route is None:
        return JSONResponse(status_code=404, content={"detail": "route_not_found"})
    target, _service = route
    body = await request.body()
    request_id = getattr(request.state, "request_id", None)
    if not isinstance(request_id, str):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
    headers: dict[str, str] = {
        "X-Request-ID": request_id,
        "X-Service-Token": settings.internal_service_token,
    }
    for header_name in ("content-type", "cookie", "authorization", "x-csrf-token"):
        header_value = request.headers.get(header_name)
        if header_value:
            headers[header_name] = header_value
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            upstream = await client.request(
                request.method,
                target,
                params=request.query_params,
                content=body,
                headers=headers,
            )
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
