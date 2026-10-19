"""Expose the transactional plane for companies, leads, and signals.

The lifespan registers ``CoreRepository``; handlers validate the internal token,
write idempotent signals, and read companies and leads for the kanban. It
relates to ``services/core/app/repository.py``, ``outbox_relay.py``, and the
``core`` migration. It is the source of truth from which analytics and ML
receive events derived from committed changes.
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from libs.common.db import check_database
from libs.common.internal_auth import require_internal_token
from libs.common.lifecycle import graceful_lifespan, instrument_app
from libs.common.logging import configure_logging
from libs.common.redis_client import check_redis
from libs.common.request_id import RequestIdMiddleware
from libs.common.settings import get_settings
from libs.contracts.api import SignalCreate
from libs.domain.pipeline import PipelineStage, group_by_stage
from services.core.app.actions import (
    ActivityCreate,
    LeadAlreadyConvertedError,
    LeadClosedError,
    OutcomeCreate,
    ProjectCreate,
    convert_to_project,
    log_activity,
    record_outcome,
)
from services.core.app.companies import CompanyUpsert, upsert_company
from services.core.app.hot_alert import HotAlertNotifier
from services.core.app.imports import (
    MAX_BYTES,
    ImportEntity,
    ImportRejectedError,
    JobNotFoundError,
    create_import,
    read_job,
)
from services.core.app.leads import (
    IdempotencyKeyReuseError,
    LeadConflictError,
    LeadCreate,
    LeadNotFoundError,
    LeadStatusPatch,
    LeadTransitionError,
    change_status,
    create_lead,
)
from services.core.app.repository import CoreRepository
from services.core.app.retention import (
    OptOutRequest,
    PersonalDataError,
    opt_out,
    purge_inactive_companies,
    reject_personal_identifiers,
)
from services.core.app.tenant_config import (
    StaleConfigError,
    TenantConfig,
    etag,
    parse_etag,
    read_config,
    update_config,
)

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    repository = CoreRepository(settings.database_url)
    app.state.repository = repository
    app.state.hot_alerts = HotAlertNotifier(settings.hot_lead_webhook_url, clock=time.monotonic)
    yield
    await app.state.hot_alerts.drain()
    await repository.close()


app = FastAPI(
    title="Invent Market Tracker Core",
    version=settings.app_version,
    lifespan=graceful_lifespan(lifespan),
)
app.add_middleware(RequestIdMiddleware)
instrument_app(app, "core")


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
        reject_personal_identifiers(payload.model_dump(mode="json"))
    except PersonalDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        reject_personal_identifiers(payload.model_dump(mode="json"))
    except PersonalDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    state: PipelineStage | None = None,
    service_line: str | None = None,
    city: str | None = None,
    segment: str | None = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        data, next_cursor = await repository.list_leads(
            limit,
            cursor,
            stage=state.value if state else None,
            service_line=service_line,
            city=city,
            segment=segment,
            min_score=min_score,
            tenant_id=x_tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_cursor") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"data": data, "next_cursor": next_cursor}


@app.get(
    "/internal/core/companies/{company_id}/brief-facts",
    dependencies=[Depends(require_internal_token)],
)
async def company_brief_facts(
    company_id: UUID,
    request: Request,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        facts = await repository.brief_facts(company_id, x_tenant_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if facts is None:
        raise HTTPException(status_code=404, detail="company_not_found")
    return facts


@app.post("/internal/core/leads", dependencies=[Depends(require_internal_token)])
async def create_lead_route(
    payload: LeadCreate,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
    x_tenant_id: Annotated[UUID, Header()],
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        body, created = await create_lead(
            repository.lead_unit_of_work(),
            payload,
            tenant_id=x_tenant_id,
            idempotency_key=idempotency_key,
            request_id=request.headers.get("X-Request-ID"),
        )
    except IdempotencyKeyReuseError as exc:
        raise HTTPException(status_code=422, detail="idempotency_key_reused") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.status_code = 201 if created else 200
    return dict(body)


@app.get("/internal/core/leads/{lead_id}", dependencies=[Depends(require_internal_token)])
async def get_lead_route(
    lead_id: UUID,
    request: Request,
    response: Response,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        lead = await repository.get_lead(lead_id, x_tenant_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if lead is None:
        raise HTTPException(status_code=404, detail="lead_not_found")
    response.headers["ETag"] = f'"{lead["version"]}"'
    return lead


@app.patch("/internal/core/leads/{lead_id}/status", dependencies=[Depends(require_internal_token)])
async def change_lead_status(
    lead_id: UUID,
    payload: LeadStatusPatch,
    request: Request,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> JSONResponse:
    try:
        repository: CoreRepository = request.app.state.repository
        body = await change_status(
            repository.lead_unit_of_work(),
            lead_id,
            payload,
            tenant_id=x_tenant_id,
            on_hot=getattr(request.app.state, "hot_alerts", None),
        )
        return JSONResponse(content=body)
    except LeadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="lead_not_found") from exc
    except LeadConflictError as exc:
        return JSONResponse(
            status_code=409, content={"detail": "version_conflict", "current": exc.current}
        )
    except LeadTransitionError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_transition", "current": exc.current, "allowed": exc.allowed},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/internal/core/pipeline/kanban", dependencies=[Depends(require_internal_token)])
async def pipeline_kanban(
    request: Request, x_tenant_id: Annotated[UUID | None, Header()] = None
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        leads_data, _next_cursor = await repository.list_leads(200, tenant_id=x_tenant_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"as_of": datetime.now(UTC).isoformat(), "columns": group_by_stage(leads_data)}


@app.post(
    "/internal/core/leads/{lead_id}/outcomes",
    status_code=201,
    dependencies=[Depends(require_internal_token)],
)
async def record_outcome_route(
    lead_id: UUID,
    payload: OutcomeCreate,
    request: Request,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        return await record_outcome(
            repository.action_unit_of_work(),
            lead_id,
            payload,
            tenant_id=x_tenant_id,
            request_id=request.headers.get("X-Request-ID"),
        )
    except LeadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="lead_not_found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post(
    "/internal/core/leads/{lead_id}/activities", dependencies=[Depends(require_internal_token)]
)
async def log_activity_route(
    lead_id: UUID,
    payload: ActivityCreate,
    request: Request,
    response: Response,
    x_tenant_id: Annotated[UUID, Header()],
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        body, created = await log_activity(
            repository.action_unit_of_work(),
            lead_id,
            payload,
            tenant_id=x_tenant_id,
            idempotency_key=idempotency_key,
            request_id=request.headers.get("X-Request-ID"),
        )
    except LeadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="lead_not_found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.status_code = 201 if created else 200
    return dict(body)


@app.post(
    "/internal/core/projects", status_code=201, dependencies=[Depends(require_internal_token)]
)
async def create_project(
    payload: ProjectCreate,
    request: Request,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        return await convert_to_project(
            repository.action_unit_of_work(),
            payload,
            tenant_id=x_tenant_id,
            request_id=request.headers.get("X-Request-ID"),
        )
    except LeadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="lead_not_found") from exc
    except LeadClosedError as exc:
        raise HTTPException(status_code=422, detail="lead_lost") from exc
    except LeadAlreadyConvertedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "lead_already_converted", "project_id": str(exc.project_id)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/internal/core/projects", dependencies=[Depends(require_internal_token)])
async def projects(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    status: str | None = None,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        data, next_cursor = await repository.list_projects(
            limit, cursor, status=status, tenant_id=x_tenant_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_cursor") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"data": data, "next_cursor": next_cursor}


@app.get("/internal/core/admin/tenant-config", dependencies=[Depends(require_internal_token)])
async def get_tenant_config(
    request: Request, response: Response, x_tenant_id: Annotated[UUID, Header()]
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        body = await read_config(repository.config_unit_of_work(), x_tenant_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.headers["ETag"] = etag(int(body["version"]))
    return body


@app.put("/internal/core/admin/tenant-config", dependencies=[Depends(require_internal_token)])
async def put_tenant_config(
    payload: TenantConfig,
    request: Request,
    response: Response,
    x_tenant_id: Annotated[UUID, Header()],
    if_match: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[UUID | None, Header()] = None,
) -> dict[str, object]:
    if if_match is None:
        raise HTTPException(status_code=428, detail="if_match_required")
    try:
        expected = parse_etag(if_match)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_if_match") from exc
    try:
        repository: CoreRepository = request.app.state.repository
        body = await update_config(
            repository.config_unit_of_work(),
            x_tenant_id,
            payload,
            if_match=expected,
            updated_by=x_user_id,
            request_id=request.headers.get("X-Request-ID"),
        )
    except StaleConfigError as exc:
        raise HTTPException(
            status_code=412,
            detail={"error": "stale_etag", "current_version": exc.current_version},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.headers["ETag"] = etag(int(body["version"]))
    return body


@app.post("/internal/core/imports/{entity}", dependencies=[Depends(require_internal_token)])
async def create_import_route(
    entity: ImportEntity,
    request: Request,
    x_tenant_id: Annotated[UUID, Header()],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
    x_filename: Annotated[str, Header(max_length=255)] = "upload.csv",
    preserve_blob: bool = False,
) -> JSONResponse:
    """Accept the CSV as the raw request body; the gateway unwraps multipart uploads."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="file_too_large")
    content = await request.body()
    try:
        repository: CoreRepository = request.app.state.repository
        job, created = await create_import(
            repository.import_unit_of_work(),
            entity,
            content,
            tenant_id=x_tenant_id,
            idempotency_key=idempotency_key,
            filename=x_filename,
            content_type=request.headers.get("content-type", "text/csv"),
            preserve_blob=preserve_blob,
        )
    except ImportRejectedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    except IdempotencyKeyReuseError as exc:
        raise HTTPException(status_code=422, detail="idempotency_key_reused") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    body = {
        "job_id": job["job_id"],
        "status": job["status"],
        "status_url": f"/api/v1/jobs/{job['job_id']}",
    }
    return JSONResponse(status_code=202 if created else 200, content=body)


@app.get("/internal/core/jobs/{job_id}", dependencies=[Depends(require_internal_token)])
async def get_job_route(
    job_id: UUID, request: Request, x_tenant_id: Annotated[UUID | None, Header()] = None
) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        return await read_job(repository.import_unit_of_work(), job_id, x_tenant_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="job_not_found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/internal/core/compliance/opt-out", dependencies=[Depends(require_internal_token)])
async def opt_out_route(payload: OptOutRequest, request: Request) -> dict[str, object]:
    try:
        repository: CoreRepository = request.app.state.repository
        return await opt_out(repository.retention_unit_of_work(), payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/internal/core/compliance/purge", dependencies=[Depends(require_internal_token)])
async def purge_route(
    request: Request,
    dry_run: bool = True,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> dict[str, object]:
    """Scheduler entrypoint; defaults to a dry run so deletion must be requested explicitly."""
    try:
        repository: CoreRepository = request.app.state.repository
        return await purge_inactive_companies(
            repository.retention_unit_of_work(), dry_run=dry_run, limit=limit
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
