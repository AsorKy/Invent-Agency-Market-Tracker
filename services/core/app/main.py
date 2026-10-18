"""Expose the transactional plane for companies, leads, and signals.

The lifespan registers ``CoreRepository``; handlers validate the internal token,
write idempotent signals, and read companies and leads for the kanban. It
relates to ``services/core/app/repository.py``, ``outbox_relay.py``, and the
``core`` migration. It is the source of truth from which analytics and ML
receive events derived from committed changes.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings
from libs.contracts.api import SignalCreate
from services.core.app.companies import CompanyUpsert, upsert_company
from services.core.app.repository import CoreRepository

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    repository = CoreRepository(settings.database_url)
    app.state.repository = repository
    yield
    await repository.close()


app = FastAPI(title="Invent Market Tracker Core", version=settings.app_version, lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "core", "version": settings.app_version}


@app.get("/ready")
async def ready() -> JSONResponse:
    database = await check_database(settings.database_url)
    redis = await check_redis(settings.redis_url)
    body = {
        "status": "ready" if database and redis else "degraded",
        "dependencies": {"database": database, "redis": redis},
    }
    return JSONResponse(status_code=200 if database and redis else 503, content=body)


@app.post("/internal/core/signals", status_code=201, dependencies=[Depends(require_internal_token)])
async def create_signal(payload: SignalCreate, request: Request) -> dict[str, str | None]:
    try:
        repository: CoreRepository = request.app.state.repository
        signal_id, event_id = await repository.create_signal(
            payload, request_id=request.headers.get("X-Request-ID")
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "signal_id": str(signal_id),
        "outbox_event_id": str(event_id) if event_id else None,
        "status": "created" if event_id else "duplicate",
    }


@app.post("/internal/core/companies/upsert", dependencies=[Depends(require_internal_token)])
async def upsert_company_route(
    payload: CompanyUpsert, request: Request, response: Response
) -> dict[str, str | int]:
    try:
        repository: CoreRepository = request.app.state.repository
        result = await upsert_company(
            repository.company_unit_of_work(),
            payload,
            request_id=request.headers.get("X-Request-ID"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.status_code = 201 if result["status"] == "created" else 200
    return {
        "status": str(result["status"]),
        "company_id": str(result["company_id"]),
        "version": int(result["version"]),
    }


@app.get("/internal/core/companies", dependencies=[Depends(require_internal_token)])
async def companies(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        data, next_cursor = await repository.list_companies(limit, cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_cursor") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"data": data, "next_cursor": next_cursor}


@app.get("/internal/core/leads", dependencies=[Depends(require_internal_token)])
async def leads(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        data, next_cursor = await repository.list_leads(limit, cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_cursor") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"data": data, "next_cursor": next_cursor}


@app.get("/internal/core/pipeline/kanban", dependencies=[Depends(require_internal_token)])
async def pipeline_kanban(request: Request) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        leads_data, _next_cursor = await repository.list_leads(200)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    columns: dict[str, list[dict[str, object]]] = {}
    for lead in leads_data:
        columns.setdefault(str(lead["stage"]), []).append(lead)
    return {"as_of": None, "columns": columns}


@app.get("/internal/core/projects", dependencies=[Depends(require_internal_token)])
async def projects() -> None:
    raise HTTPException(status_code=501, detail="project_read_model_pending")
