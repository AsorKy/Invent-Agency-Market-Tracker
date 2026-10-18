"""Expose the ARCH-0001 v2.2 Brief digest shell.

The application serves company and lead digest paths, applies
stale-while-revalidate headers, coordinates regeneration with a Redis
single-flight lock, and falls back to deterministic rules when no model
service exists. It relates to ``repository.py``, ``rule_based.py``, and
``single_flight.py`` and is used as the isolated Brief backend behind the
gateway.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis, create_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings
from services.brief.app.core_source import (
    CoreNotFound,
    CoreSource,
    CoreUnavailable,
    HttpCoreSource,
)
from services.brief.app.models import BriefResponse
from services.brief.app.repository import BriefRepository
from services.brief.app.service import BriefService
from services.brief.app.single_flight import SingleFlightLock

settings = get_settings()
configure_logging(settings.log_level)


class BriefState:
    """Hold service-local resources exposed through FastAPI application state."""

    def __init__(
        self,
        repository: BriefRepository,
        redis: Redis,
        core: CoreSource,
    ) -> None:
        self.repository = repository
        self.redis = redis
        self.core = core
        self.service = BriefService(
            repository=repository,
            lock=SingleFlightLock(redis, ttl_ms=30_000),
            core=core,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create and close the repository, Redis, and core client resources."""
    redis = create_redis(settings.redis_url)
    core = HttpCoreSource(settings.core_url, settings.internal_service_token)
    state = BriefState(BriefRepository(settings.database_url), redis, core)
    app.state.brief = state
    yield
    await core.close()
    await state.repository.close()
    await state.redis.aclose()


app = FastAPI(title="Invent Market Tracker Brief", version=settings.app_version, lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)


async def _digest_response(
    request: Request,
    background_tasks: BackgroundTasks,
    *,
    company_id: UUID,
    lead_id: UUID | None,
    tenant_id: UUID | None,
) -> JSONResponse:
    """Resolve a digest and apply the v2.2 cache state headers."""
    state: BriefState = request.app.state.brief
    try:
        result, pending = await state.service.read(company_id, tenant_id)
    except CoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="db_unavailable") from exc
    except CoreNotFound as exc:
        raise HTTPException(status_code=404, detail="not_found") from exc
    if pending is not None:
        background_tasks.add_task(pending.run)
    payload = BriefResponse(
        **result.digest.model_dump(), lead_id=lead_id, stale=result.stale
    ).model_dump(mode="json")
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": "private, max-age=60, stale-while-revalidate=300",
            "X-Brief-Cache": result.cache_state,
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Report process liveness without checking external dependencies."""
    return {"status": "ok", "service": "brief", "version": settings.app_version}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Report whether Brief can reach its database and Redis dependencies."""
    database = await check_database(settings.database_url)
    redis = await check_redis(settings.redis_url)
    ready_state = database and redis
    return JSONResponse(
        status_code=200 if ready_state else 503,
        content={
            "status": "ready" if ready_state else "degraded",
            "dependencies": {"database": database, "redis": redis},
        },
    )


@app.get(
    "/internal/brief/companies/{company_id}/digest",
    dependencies=[Depends(require_internal_token)],
)
@app.get("/internal/brief/companies/{company_id}", dependencies=[Depends(require_internal_token)])
async def company_digest(
    company_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> JSONResponse:
    """Return a company digest using stale-while-revalidate semantics."""
    return await _digest_response(
        request,
        background_tasks,
        company_id=company_id,
        lead_id=None,
        tenant_id=x_tenant_id,
    )


@app.get("/internal/brief/leads/{lead_id}/digest", dependencies=[Depends(require_internal_token)])
@app.get("/internal/brief/leads/{lead_id}", dependencies=[Depends(require_internal_token)])
async def lead_digest(
    lead_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> JSONResponse:
    """Return a lead digest through the company-oriented Brief model."""
    return await _digest_response(
        request,
        background_tasks,
        company_id=lead_id,
        lead_id=lead_id,
        tenant_id=x_tenant_id,
    )
