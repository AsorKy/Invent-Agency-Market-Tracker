"""Expose the F1 score calculation and current company score.

The endpoint accepts bounded features, acquires a short-lived Redis lock,
calculates the pure rule, and persists history before publishing the score
event. It relates to ``libs/domain/scoring.py``, ``MlRepository``, and
``services/ml/app/consumer.py``, which invalidates scores after data changes.
It prioritizes leads audibly while outcomes accumulate.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis, create_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings
from libs.contracts.api import ScoreFeatures, ScoreResponse
from libs.domain.scoring import calculate_rule_score
from services.ml.app.repository import MlRepository

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.repository = MlRepository(settings.database_url)
    app.state.redis = create_redis(settings.redis_url)
    yield
    await app.state.repository.close()
    await app.state.redis.aclose()


app = FastAPI(title="Invent Market Tracker ML", version=settings.app_version, lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)


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
async def regenerate_score(features: ScoreFeatures, request: Request) -> ScoreResponse:
    lock_key = f"lock:score:{features.company_id}"
    owner_id = str(uuid4())
    acquired = await request.app.state.redis.set(lock_key, owner_id, nx=True, px=30_000)
    if not acquired:
        previous = await request.app.state.repository.latest_score(features.company_id)
        return ScoreResponse(
            company_id=features.company_id,
            score=int(previous["score"]) if previous else 0,
            status="in_progress",
            stale=True,
            score_version=str(previous["score_version"]) if previous else "pending",
        )

    try:
        breakdown = calculate_rule_score(
            icp_fit=features.icp_fit,
            signal_strength=features.signal_strength,
            territory_opportunity=features.territory_opportunity,
        )
        score_version = f"rule-f1:{features.source_version}"
        await request.app.state.repository.upsert_score(
            company_id=features.company_id,
            score=breakdown.score,
            is_hot=breakdown.is_hot,
            source_version=features.source_version,
            score_version=score_version,
        )
        await request.app.state.redis.xadd(
            "invent.events",
            {
                "event_id": str(uuid4()),
                "event_type": "lead.score_updated",
                "aggregate_type": "company",
                "aggregate_id": str(features.company_id),
                "score": str(breakdown.score),
                "is_hot": str(breakdown.is_hot).lower(),
            },
        )
        return ScoreResponse(
            company_id=features.company_id,
            score=breakdown.score,
            status="computed",
            score_version=score_version,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        release_lock_script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] "
            "then return redis.call('del', KEYS[1]) else return 0 end"
        )
        await request.app.state.redis.eval(
            release_lock_script,
            1,
            lock_key,
            owner_id,
        )


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
