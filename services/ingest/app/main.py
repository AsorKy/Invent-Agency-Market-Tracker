"""Receive Bright Data snapshots and hand them to the ingest pipeline.

The webhook validates the contract, calculates a stable payload hash, and
delegates persistence, idempotency, and enqueueing to ``IngestRepository``;
the processing endpoint remains a contract placeholder for the next iteration.
It relates to ``libs/contracts/api.py``, ``services/ingest/app/processor.py``,
and ``orchestration/n8n/wf_maps_medellin.json``. It accepts external data
without blocking the provider response.
"""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import orjson
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings
from libs.contracts.api import BrightDataWebhook
from services.ingest.app.repository import IngestRepository

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.repository = IngestRepository(settings.database_url, settings.redis_url)
    yield
    await app.state.repository.close()


app = FastAPI(title="Invent Market Tracker Ingest", version=settings.app_version, lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ingest", "version": settings.app_version}


@app.get("/ready")
async def ready() -> JSONResponse:
    database = await check_database(settings.database_url)
    redis = await check_redis(settings.redis_url)
    body = {
        "status": "ready",
        "dependencies": {"database": database, "redis": redis},
    }
    if not database or not redis:
        body["status"] = "degraded"
    return JSONResponse(status_code=200 if database and redis else 503, content=body)


@app.post("/internal/ingest/webhook/bd", status_code=202)
async def bright_data_webhook(payload: BrightDataWebhook, request: Request) -> dict[str, object]:
    normalized = orjson.dumps(payload.payload, option=orjson.OPT_SORT_KEYS)
    payload_sha256 = hashlib.sha256(normalized).hexdigest()
    try:
        repository: IngestRepository = request.app.state.repository
        row_id, created, queued = await repository.persist_snapshot(
            snapshot_id=payload.snapshot_id,
            dataset_id=payload.dataset_id,
            payload=payload.payload,
            payload_sha256=payload_sha256,
            fetched_at=payload.fetched_at,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "accepted" if created else "duplicate",
        "raw_snapshot_id": str(row_id),
        "snapshot_id": payload.snapshot_id,
        "dataset_id": payload.dataset_id,
        "payload_sha256": payload_sha256,
        "received_at": datetime.now(UTC),
        "next_step": "processor_queued" if queued else "already_queued",
    }


@app.post(
    "/internal/ingest/process/{snapshot_id}",
    status_code=202,
    dependencies=[Depends(require_internal_token)],
)
async def process_snapshot(snapshot_id: str) -> dict[str, str]:
    return {"status": "queued", "snapshot_id": snapshot_id}
