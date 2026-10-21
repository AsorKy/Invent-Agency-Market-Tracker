"""Expose the ARCH-0001 v2.2 Pulse metric shell.

The application owns the five operational metric endpoints, the market and
segment endpoints merged from the former analytics service, common freshness
metadata, browser cache headers, and empty-data behavior. It relates to
``services/pulse/app/repository.py`` for read-model access and to the shared
health helpers used by the other backend services. It is used behind the
gateway while the event-driven Pulse projections are implemented.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.lifecycle import graceful_lifespan, instrument_app
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings
from libs.contracts.pulse import CACHE_CONTROL, is_stale
from services.pulse.app.descriptive import concentration, describe
from services.pulse.app.models import (
    OpsHealthResponse,
    PipelineBacklogResponse,
    PipelineTimeResponse,
    SourceEfficiencyResponse,
    SourceFreshness,
    TerritoryOpportunityResponse,
    TopSourcesResponse,
    UnprocessedSignalsResponse,
)
from services.pulse.app.repository import PulseRepository
from services.pulse.app.socio_loader import SocioDataError

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create and close the service-local read repository."""
    repository = PulseRepository(settings.database_url, settings.socio_data_dir)
    app.state.repository = repository
    yield
    await repository.close()


app = FastAPI(
    title="Invent Market Tracker Pulse",
    version=settings.app_version,
    lifespan=graceful_lifespan(lifespan),
)
app.add_middleware(RequestIdMiddleware)
instrument_app(app, "pulse")


@app.exception_handler(SQLAlchemyError)
@app.exception_handler(OSError)
@app.exception_handler(RuntimeError)
async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    """Answer 503 instead of 500 when a metric read cannot reach the database."""
    structlog.get_logger("pulse").warning(
        "pulse_database_unavailable", path=request.url.path, error=type(exc).__name__
    )
    return JSONResponse(status_code=503, content={"detail": "database_unavailable"})


@app.middleware("http")
async def pulse_cache_header(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Add the browser cache header to every successful Pulse response."""
    response = await call_next(request)
    if request.url.path.startswith("/internal/pulse/") and response.status_code == 200:
        response.headers["Cache-Control"] = CACHE_CONTROL
    return response


def _now_ms() -> int:
    """Return the current instant as UTC epoch milliseconds."""
    return int(datetime.now(UTC).timestamp() * 1000)


def _freshness(as_of: datetime | None) -> tuple[int, bool]:
    """Convert a source timestamp to API metadata and apply the 26-hour rule."""
    as_of_ms = int(as_of.timestamp() * 1000) if as_of is not None else _now_ms()
    return as_of_ms, is_stale(as_of_ms, _now_ms())


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


@app.get("/internal/pulse/market", dependencies=[Depends(require_internal_token)])
@app.get("/internal/pulse/market/overview", dependencies=[Depends(require_internal_token)])
async def market_overview(
    request: Request,
    x_tenant_id: Annotated[UUID | None, Header(alias="X-Tenant-Id")] = None,
) -> dict[str, object]:
    """Return 90-day market totals from the Pulse daily rollup."""
    repository: PulseRepository = request.app.state.repository
    try:
        return await repository.market_overview(tenant_id=x_tenant_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/internal/pulse/segments", dependencies=[Depends(require_internal_token)])
async def segments() -> None:
    """Report that the segment read model is not built yet."""
    raise HTTPException(status_code=501, detail="segment_read_model_pending")


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
    payload = SourceEfficiencyResponse(
        sources=rows,
        as_of=as_of,
        stale=stale,
        leads_concentration=concentration([r.leads for r in rows]) if rows else None,
    )
    return _json_response(payload.model_dump(mode="json"), as_of=as_of)


@app.get("/internal/pulse/time-in-pipeline", dependencies=[Depends(require_internal_token)])
async def time_in_pipeline(
    request: Request,
    group_by: str = Query(default="service_line"),
) -> JSONResponse:
    """Return pipeline duration groups and low-sample flags."""
    rows, source_as_of = await request.app.state.repository.time_in_pipeline(group_by=group_by)
    as_of, stale = _freshness(source_as_of)
    payload = PipelineTimeResponse(
        groups=rows,
        as_of=as_of,
        stale=stale,
        p50_days_stats=describe([r.p50_days for r in rows if r.p50_days is not None])
        if rows
        else None,
    )
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
    payload = TopSourcesResponse(
        ranking=rows,
        as_of=as_of,
        stale=stale,
        qualified_leads_concentration=concentration([r.qualified_leads for r in rows])
        if rows
        else None,
    )
    return _json_response(payload.model_dump(mode="json"), as_of=as_of)


@app.get("/internal/pulse/ops/health", dependencies=[Depends(require_internal_token)])
async def ops_health(request: Request) -> JSONResponse:
    """Return per-source freshness and outbox backlog for the Ops page."""
    sources, backlog, source_as_of = await request.app.state.repository.ops_health()
    as_of, stale = _freshness(source_as_of)
    now = _now_ms()
    rows = [
        SourceFreshness(
            source=row.source,
            as_of=row.as_of,
            stale=row.as_of is not None and is_stale(row.as_of, now),
        )
        for row in sources
    ]
    payload = OpsHealthResponse(sources=rows, outbox_backlog=backlog, as_of=as_of, stale=stale)
    return _json_response(payload.model_dump(mode="json"), as_of=as_of)


@app.get("/internal/pulse/territory/opportunity", dependencies=[Depends(require_internal_token)])
async def territory_opportunity(
    request: Request,
    city: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=500),
) -> JSONResponse:
    """Return territories ranked by the opportunity index IOP."""
    try:
        (
            rows,
            source_as_of,
            method_version,
        ) = await request.app.state.repository.territory_opportunity(city=city, limit=limit)
    except SocioDataError as exc:
        raise HTTPException(status_code=503, detail="socio_data_invalid") from exc
    as_of, stale = _freshness(source_as_of)
    payload = TerritoryOpportunityResponse(
        territories=rows, as_of=as_of, stale=stale, method_version=method_version
    )
    return _json_response(payload.model_dump(mode="json"), as_of=as_of)
