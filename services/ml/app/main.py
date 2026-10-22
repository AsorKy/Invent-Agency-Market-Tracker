"""Expose the F1 score calculation and current company score.

The endpoint accepts bounded features, acquires a short-lived Redis lock,
calculates the pure rule, and persists history before publishing the score
event. It relates to ``libs/domain/scoring.py``, ``MlRepository``, and
``services/ml/app/consumer.py``, which invalidates scores after data changes.
It prioritizes leads audibly while outcomes accumulate.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.lifecycle import graceful_lifespan, instrument_app
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis, create_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings
from libs.contracts.api import ScoreFeatures, ScoreResponse
from services.ml.app.core_rules import HttpCoreRules
from services.ml.app.explanations_routes import router as explanations_router
from services.ml.app.repository import MlRepository
from services.ml.app.scoring_service import ScoringService

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.repository = MlRepository(
        settings.database_url,
        HttpCoreRules(settings.core_url, settings.internal_service_token),
    )
    app.state.redis = create_redis(settings.redis_url)
    yield
    await app.state.repository.close()
    await app.state.redis.aclose()


app = FastAPI(
    title="Invent Market Tracker ML",
    version=settings.app_version,
    lifespan=graceful_lifespan(lifespan),
)
app.add_middleware(RequestIdMiddleware)
instrument_app(app, "ml")
app.include_router(explanations_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ml", "version": settings.app_version}


@app.get("/ready")
async def ready() -> JSONResponse:
    database = await check_database(settings.database_url)
    redis = await check_redis(settings.redis_url)
    body = {
        "status": "ready" if database and redis else "degraded",
        "dependencies": {"database": database, "redis": redis},
    }
    return JSONResponse(status_code=200 if database and redis else 503, content=body)


@app.post(
    "/internal/ml/scores",
    response_model=ScoreResponse,
    dependencies=[Depends(require_internal_token)],
)
async def regenerate_score(
    features: ScoreFeatures,
    request: Request,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> ScoreResponse:
    repository: MlRepository = request.app.state.repository
    service = ScoringService(
        repository, request.app.state.redis, lock_ttl_seconds=settings.score_lock_ttl_seconds
    )
    try:
        weights = await repository.load_weights(x_tenant_id) if x_tenant_id else None
        return await service.regenerate(features, weights, x_tenant_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/internal/ml/scores/{company_id}", dependencies=[Depends(require_internal_token)])
async def get_score(company_id: UUID, request: Request) -> dict[str, object]:
    try:
        repository: MlRepository = request.app.state.repository
        score = await repository.latest_score(company_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if score is None:
        raise HTTPException(status_code=404, detail="score_not_found")
    return score
