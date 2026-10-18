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
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis, create_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings
from services.brief.app.models import BriefResponse, StoredDigest
from services.brief.app.repository import BriefRepository
from services.brief.app.rule_based import build_rule_based_content
from services.brief.app.single_flight import SingleFlightLock

settings = get_settings()
configure_logging(settings.log_level)


class BriefState:
    """Hold service-local resources exposed through FastAPI application state."""

    def __init__(self, repository: BriefRepository, redis: Redis) -> None:
        self.repository = repository
        self.lock = SingleFlightLock(redis, ttl_ms=settings.score_lock_ttl_seconds * 1000)
        self.redis = redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create and close the repository and Redis lock resources."""
    redis = create_redis(settings.redis_url)
    state = BriefState(BriefRepository(settings.database_url), redis)
    app.state.brief = state
    yield
    await state.repository.close()
    await state.redis.aclose()


app = FastAPI(title="Invent Market Tracker Brief", version=settings.app_version, lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)


def _now_ms() -> int:
    """Return the current instant as UTC epoch milliseconds."""
    return int(datetime.now(UTC).timestamp() * 1000)


def _fallback_digest(company_id: UUID, source_version: int) -> StoredDigest:
    """Build a deterministic degraded digest record."""
    return StoredDigest(
        company_id=company_id,
        source_version=source_version,
        content=build_rule_based_content(company_id),
        generated_at=_now_ms(),
        summarizer_version="rule-v3",
        degraded=True,
    )


async def _regenerate(
    state: BriefState,
    company_id: UUID,
    source_version: int,
) -> StoredDigest:
    """Regenerate a digest under single-flight protection."""
    key = f"brief:lock:{company_id}"
    owner = uuid4().hex
    if not await state.lock.acquire(key, owner):
        latest = await state.repository.get_latest(company_id)
        return latest or _fallback_digest(company_id, source_version)
    try:
        latest = await state.repository.get_latest(company_id)
        if latest is not None and latest.source_version >= source_version:
            return latest
        digest = _fallback_digest(company_id, source_version)
        await state.repository.save(digest)
        return digest
    finally:
        await state.lock.release(key, owner)


async def _digest_response(
    request: Request,
    background_tasks: BackgroundTasks,
    *,
    company_id: UUID,
    lead_id: UUID | None,
    source_version: int,
) -> JSONResponse:
    """Resolve a digest and apply the v2.2 cache state headers."""
    state: BriefState = request.app.state.brief
    latest = await state.repository.get_latest(company_id)
    if latest is None:
        digest = await _regenerate(state, company_id, source_version)
        stale = False
        cache_state = "degraded" if digest.degraded else "hit"
    elif latest.source_version == source_version:
        digest = latest
        stale = False
        cache_state = "degraded" if digest.degraded else "hit"
    else:
        digest = latest
        stale = True
        cache_state = "stale"
        background_tasks.add_task(_regenerate, state, company_id, source_version)

    payload = BriefResponse(**digest.model_dump(), lead_id=lead_id, stale=stale).model_dump(
        mode="json"
    )
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": "private, max-age=60, stale-while-revalidate=300",
            "X-Brief-Cache": cache_state,
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
    source_version: int = Query(default=1, ge=1),
) -> JSONResponse:
    """Return a company digest using stale-while-revalidate semantics."""
    return await _digest_response(
        request,
        background_tasks,
        company_id=company_id,
        lead_id=None,
        source_version=source_version,
    )


@app.get("/internal/brief/leads/{lead_id}/digest", dependencies=[Depends(require_internal_token)])
@app.get("/internal/brief/leads/{lead_id}", dependencies=[Depends(require_internal_token)])
async def lead_digest(
    lead_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    source_version: int = Query(default=1, ge=1),
) -> JSONResponse:
    """Return a lead digest through the company-oriented Brief model."""
    return await _digest_response(
        request,
        background_tasks,
        company_id=lead_id,
        lead_id=lead_id,
        source_version=source_version,
    )
