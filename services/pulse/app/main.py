"""Expose the ARCH-0001 v2.2 Pulse metric shell.

The application owns the five operational metric endpoints, common freshness
metadata, browser cache headers, and empty-data behavior. It relates to
``services/pulse/app/repository.py`` for read-model access and to the shared
health helpers used by the other backend services. It is used behind the
gateway while the event-driven Pulse projections are implemented.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings
from services.pulse.app.models import (
    PipelineBacklogResponse,
    PipelineTimeResponse,
    SourceEfficiencyResponse,
    TopSourcesResponse,
    UnprocessedSignalsResponse,
)
from services.pulse.app.repository import PulseRepository

settings = get_settings()
configure_logging(settings.log_level)

STALE_AFTER_MS = 26 * 60 * 60 * 1000
CACHE_CONTROL = "private, max-age=60"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create and close the service-local read repository."""
    repository = PulseRepository(settings.database_url)
    app.state.repository = repository
    yield
    await repository.close()


app = FastAPI(title="Invent Market Tracker Pulse", version=settings.app_version, lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)


def _now_ms() -> int:
    """Return the current instant as UTC epoch milliseconds."""
    return int(datetime.now(UTC).timestamp() * 1000)


def _freshness(as_of: datetime | None) -> tuple[int, bool]:
    """Convert a source timestamp to API metadata and apply the 26-hour rule."""
    as_of_ms = int(as_of.timestamp() * 1000) if as_of is not None else _now_ms()
    return as_of_ms, _now_ms() - as_of_ms > STALE_AFTER_MS


def _json_response(payload: object, *, as_of: int) -> JSONResponse:
    """Return a Pulse response with the contract cache headers."""
    return JSONResponse(
        content=payload,
        headers={"X-As-Of": str(as_of), "Cache-Control": CACHE_CONTROL},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Report process liveness without checking external dependencies."""
    return {"status": "ok", "service": "pulse", "version": settings.app_version}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Report whether Pulse can reach its database and Redis dependencies."""
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
    "/internal/pulse/unprocessed-signals",
    dependencies=[Depends(require_internal_token)],
)
async def unprocessed_signals(
    request: Request,
    granularity: str = Query(default="week"),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    city: str | None = Query(default=None),
) -> JSONResponse:
    """Return unprocessed signal points with safe empty-data semantics."""
    rows, source_as_of = await request.app.state.repository.unprocessed_signals(
        granularity=granularity,
        from_date=from_date,
        to_date=to_date,
        city=city,
    )
    as_of, stale = _freshness(source_as_of)
    payload = UnprocessedSignalsResponse(points=rows, as_of=as_of, stale=stale)
    return _json_response(payload.model_dump(mode="json"), as_of=as_of)


@app.get("/internal/pulse/pipeline-backlog", dependencies=[Depends(require_internal_token)])
async def pipeline_backlog(
    request: Request,
    city: str | None = Query(default=None),
) -> JSONResponse:
    """Return pipeline backlog metrics without turning missing data into zero."""
    metrics, source_as_of = await request.app.state.repository.pipeline_backlog(city=city)
    as_of, stale = _freshness(source_as_of)
    payload = PipelineBacklogResponse(
        active=metrics.active if metrics else None,
        hot=metrics.hot if metrics else None,
        capacity_estimate=metrics.capacity_estimate if metrics else None,
        as_of=as_of,
        stale=stale,
    )
    return _json_response(payload.model_dump(mode="json"), as_of=as_of)


@app.get("/internal/pulse/source-efficiency", dependencies=[Depends(require_internal_token)])
async def source_efficiency(
    request: Request,
    period: str = Query(default="30d"),
    service_line: str | None = Query(default=None),
) -> JSONResponse:
    """Return source efficiency rows with nullable money metrics."""
    rows, source_as_of = await request.app.state.repository.source_efficiency(
        period=period, service_line=service_line
    )
    as_of, stale = _freshness(source_as_of)
    payload = SourceEfficiencyResponse(sources=rows, as_of=as_of, stale=stale)
    return _json_response(payload.model_dump(mode="json"), as_of=as_of)


@app.get("/internal/pulse/time-in-pipeline", dependencies=[Depends(require_internal_token)])
async def time_in_pipeline(
    request: Request,
    group_by: str = Query(default="service_line"),
) -> JSONResponse:
    """Return pipeline duration groups and low-sample flags."""
    rows, source_as_of = await request.app.state.repository.time_in_pipeline(group_by=group_by)
    as_of, stale = _freshness(source_as_of)
    payload = PipelineTimeResponse(groups=rows, as_of=as_of, stale=stale)
    return _json_response(payload.model_dump(mode="json"), as_of=as_of)


@app.get("/internal/pulse/top-sources", dependencies=[Depends(require_internal_token)])
async def top_sources(
    request: Request,
    k: int = Query(default=10, ge=1, le=100),
    service_line: str = Query(default="all"),
) -> JSONResponse:
    """Return a bounded qualified-lead source ranking."""
    rows, source_as_of = await request.app.state.repository.top_sources(
        limit=k, service_line=service_line
    )
    as_of, stale = _freshness(source_as_of)
    payload = TopSourcesResponse(ranking=rows, as_of=as_of, stale=stale)
    return _json_response(payload.model_dump(mode="json"), as_of=as_of)
