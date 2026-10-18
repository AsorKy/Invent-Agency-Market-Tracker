"""Receive Bright Data snapshots and hand them to the ingest pipeline.

The webhook validates the contract, calculates a stable payload hash, and
delegates persistence, idempotency, and enqueueing to ``IngestRepository``;
the processing endpoint remains a contract placeholder for the next iteration.
It also exposes the token-protected budget check used by n8n, a trigger endpoint
that tracks collections in ``ingest.pending_snapshot``, and starts the polling
fallback (REQ-ING-008) when enabled. It relates to ``libs/contracts/api.py``,
``services/ingest/app/processor.py``,
and ``orchestration/n8n/wf_maps_medellin.json``. It accepts external data
without blocking the provider response.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.lifecycle import graceful_lifespan, instrument_app
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.security import valid_signed_webhook
from libs.common.settings import get_settings
from libs.contracts.api import BrightDataWebhook
from services.ingest.app.budget import BudgetRepository
from services.ingest.app.connectors import ConnectorError
from services.ingest.app.poll import (
    PendingSnapshotRepository,
    SnapshotPoller,
    trigger_and_track,
)
from services.ingest.app.repository import IngestRepository
from services.ingest.app.snapshot import payload_sha256 as compute_payload_sha256
from services.ingest.app.source_config import (
    build_connectors,
    notify_url,
    validate_live_credentials,
)

settings = get_settings()
validate_live_credentials(settings)
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.repository = IngestRepository(
        settings.database_url,
        settings.redis_url,
        enqueue_mark_ttl_seconds=settings.ingest_enqueue_mark_ttl_seconds,
    )
    engine = getattr(app.state.repository, "engine", None)
    app.state.budget = BudgetRepository(engine) if engine else None
    app.state.connectors = build_connectors(settings)
    app.state.pending = PendingSnapshotRepository(engine) if engine else None
    poll_task: asyncio.Task[None] | None = None
    if settings.ingest_poll_enabled and app.state.pending is not None:
        poller = SnapshotPoller(
            app.state.pending, app.state.connectors, app.state.repository.persist_snapshot
        )
        poll_task = asyncio.create_task(poller.run_forever(settings.ingest_poll_interval_seconds))
    yield
    if poll_task is not None:
        poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poll_task
    await app.state.repository.close()


app = FastAPI(
    title="Invent Market Tracker Ingest",
    version=settings.app_version,
    lifespan=graceful_lifespan(lifespan),
)
app.add_middleware(RequestIdMiddleware)
instrument_app(app, "ingest")


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
    raw_body = await request.body()
    if not valid_signed_webhook(
        raw_body,
        signature=request.headers.get("X-BrightData-Signature"),
        timestamp=request.headers.get("X-BrightData-Timestamp"),
        secret=settings.bright_data_webhook_secret,
        replay_window_seconds=settings.bright_data_replay_window_seconds,
    ):
        raise HTTPException(status_code=401, detail="invalid_bright_data_signature")
    payload_sha256 = compute_payload_sha256(payload.payload)
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


def get_budget_repository(request: Request) -> BudgetRepository:
    """Return the budget repository; tests override this dependency."""
    budget: BudgetRepository | None = request.app.state.budget
    if budget is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL is required for budgets")
    return budget


@app.get("/internal/ingest/budget/{source}", dependencies=[Depends(require_internal_token)])
async def source_budget(
    source: str, budget: Annotated[BudgetRepository, Depends(get_budget_repository)]
) -> dict[str, Any]:
    """Return 200 while the monthly budget has records left, 429 otherwise."""
    remaining = await budget.remaining(source, datetime.now(UTC))
    if remaining <= 0:
        raise HTTPException(status_code=429, detail="budget_exhausted")
    return {"source": source, "remaining": remaining}


@app.post(
    "/internal/ingest/trigger/{source}",
    status_code=202,
    dependencies=[Depends(require_internal_token)],
)
async def trigger_source(
    source: str,
    inputs: list[dict[str, Any]],
    request: Request,
    budget: Annotated[BudgetRepository, Depends(get_budget_repository)],
) -> dict[str, str]:
    """Trigger a collection for a source and track it for polling."""
    connector = request.app.state.connectors.get(source)
    if connector is None or request.app.state.pending is None:
        raise HTTPException(status_code=404, detail="source_not_available")
    if await budget.remaining(source, datetime.now(UTC)) <= 0:
        raise HTTPException(status_code=429, detail="budget_exhausted")
    try:
        snapshot_id = await trigger_and_track(
            connector, request.app.state.pending, inputs, notify_url=notify_url(settings)
        )
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail="provider_error") from exc
    return {"status": "triggered", "source": source, "snapshot_id": snapshot_id}
