"""Expose the identity service bootstrap contract.

This module provides health, readiness, and JWKS discovery endpoints so the
v2.2 service topology can be started locally. It relates to the gateway,
``libs.common.settings``, and the future Django/DRF identity implementation.
It deliberately does not issue production JWTs; production identity remains a
separate migration step that must provide signed keys, RBAC, tenants, sessions,
and the private admin surface described by ARCH-0001.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

from libs.common.internal_auth import require_internal_token
from libs.common.logging import configure_logging
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start and stop the identity bootstrap without external state."""
    yield


app = FastAPI(
    title="Invent Market Tracker Identity Bootstrap",
    version=settings.app_version,
    lifespan=lifespan,
)
app.add_middleware(RequestIdMiddleware)


@app.get("/health")
async def health() -> dict[str, str]:
    """Return liveness information for the identity container."""
    return {"status": "ok", "service": "identity", "version": settings.app_version}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Report that the local identity bootstrap is available."""
    return JSONResponse(
        status_code=200,
        content={"status": "ready", "service": "identity", "mode": "bootstrap"},
    )


@app.get("/.well-known/jwks.json")
async def jwks() -> dict[str, list[object]]:
    """Expose an empty local JWKS until the identity issuer is installed."""
    return {"keys": []}


@app.post(
    "/internal/identity/auth/session",
    dependencies=[Depends(require_internal_token)],
    status_code=501,
)
async def create_session() -> None:
    """Reserve the session endpoint until the Django identity issuer is installed."""
    raise HTTPException(status_code=501, detail="identity_issuer_pending")


@app.get("/internal/identity/me", dependencies=[Depends(require_internal_token)])
async def current_user() -> None:
    """Fail closed when no signed identity session is available."""
    raise HTTPException(status_code=401, detail="authentication_required")


@app.post("/internal/identity/logout", dependencies=[Depends(require_internal_token)])
async def logout() -> Response:
    """Reserve logout for the future session store and return no content."""
    return Response(status_code=204)
