"""Expose the initial read API for the analytics service.

The application creates ``AnalyticsRepository`` during its lifespan, checks
Postgres and Redis through ``/ready``, and serves market and territory
opportunity rollups. It relates to ``services/analytics/app/consumer.py``,
which materializes signals from ``invent.events``, and the dbt models in
``transform``. It is used as the gateway backend for market decisions.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings
from services.analytics.app.repository import AnalyticsRepository

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    repository = AnalyticsRepository(settings.database_url)
    app.state.repository = repository
    yield
    await repository.close()


app = FastAPI(
    title="Invent Market Tracker Analytics", version=settings.app_version, lifespan=lifespan
)
app.add_middleware(RequestIdMiddleware)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "analytics", "version": settings.app_version}


@app.get("/ready")
async def ready() -> JSONResponse:
    database = await check_database(settings.database_url)
    redis = await check_redis(settings.redis_url)
    body = {
        "status": "ready" if database and redis else "degraded",
        "dependencies": {"database": database, "redis": redis},
    }
    return JSONResponse(status_code=200 if database and redis else 503, content=body)


@app.get("/internal/analytics/market", dependencies=[Depends(require_internal_token)])
@app.get("/internal/analytics/market/overview", dependencies=[Depends(require_internal_token)])
async def market_overview(request: Request) -> dict[str, object]:
    try:
        repository: AnalyticsRepository = request.app.state.repository
        return await repository.market_overview()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get(
    "/internal/analytics/territory/opportunity", dependencies=[Depends(require_internal_token)]
)
async def territory_opportunity(request: Request) -> dict[str, object]:
    try:
        rows = await request.app.state.repository.territory_opportunity()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"data": rows, "as_of": max((row["as_of"] for row in rows), default=None)}


@app.get("/internal/analytics/segments", dependencies=[Depends(require_internal_token)])
async def segments() -> None:
    raise HTTPException(status_code=501, detail="segment_read_model_pending")


@app.get("/internal/analytics/ops/health", dependencies=[Depends(require_internal_token)])
async def ops_health() -> dict[str, object]:
    return {
        "service": "analytics",
        "status": "draft",
        "freshness": {"source_registry": "pending", "rollups": "pending"},
    }
