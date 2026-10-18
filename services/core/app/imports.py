"""Accept CSV uploads as background jobs and process them in validated chunks.

``create_import`` checks size, row count, header, the idempotency key and the limit of 3 active
jobs per tenant, then stores the upload blob and the job in one transaction (HTTP 202).
``process_next_job`` is the worker side: it claims a queued job, parses the CSV in chunks of 5,000
rows behind ``parse_chunks`` (stdlib ``csv``; a Polars parser can replace it without changing
callers), validates each row with Pydantic, deduplicates by ``row_sha256`` per tenant and entity,
and applies the existing company, lead and activity writers, which emit one outbox event per
changed aggregate. Every chunk commits on its own so progress is visible, a bad row is isolated in
a savepoint and counted as an error, and the blob content is purged when the job ends unless
``preserve_blob`` was requested (REQ-ING-005). It relates to ``main.py``, ``worker.py``,
``companies.py``, ``leads.py``, ``actions.py`` and ``repository.py``.
"""

import csv
import hashlib
import io
import json
from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from enum import StrEnum
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from services.core.app.actions import ActivityCreate, ActivityType, log_activity
from services.core.app.companies import CompanyUpsert, upsert_company
from services.core.app.leads import IdempotencyKeyReuseError, LeadCreate, create_lead

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
MAX_ACTIVE_JOBS = 3
CHUNK_ROWS = 5_000


class ImportEntity(StrEnum):
    COMPANIES = "companies"
    LEADS = "leads"
    ACTIVITIES = "activities"


class ImportRejectedError(Exception):
    """Upload refused before a job exists; ``code`` maps to the HTTP detail."""

    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class JobNotFoundError(Exception):
    pass


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value.strip() if isinstance(value, str) else value


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Any:
        return _blank_to_none(value)


class CompanyRow(_Row):
    legal_name: str = Field(min_length=1, max_length=300)
    nit: str | None = Field(default=None, max_length=32)
    trade_name: str | None = Field(default=None, max_length=300)
    sector_code: str | None = Field(default=None, max_length=16)
    employee_count: int | None = Field(default=None, ge=0)
    website: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=120)


class LeadRow(_Row):
    company_id: UUID
    service_line: str = Field(min_length=1, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    source: str | None = Field(default=None, max_length=120)


class ActivityRow(_Row):
    lead_id: UUID
    activity_type: ActivityType
    channel: str | None = Field(default=None, max_length=60)
    source: str | None = Field(default=None, max_length=60)
    occurred_at: datetime | None = None


ROW_MODELS: dict[ImportEntity, type[_Row]] = {
    ImportEntity.COMPANIES: CompanyRow,
    ImportEntity.LEADS: LeadRow,
    ImportEntity.ACTIVITIES: ActivityRow,
}
REQUIRED_COLUMNS: dict[ImportEntity, set[str]] = {
    ImportEntity.COMPANIES: {"legal_name"},
    ImportEntity.LEADS: {"company_id", "service_line"},
    ImportEntity.ACTIVITIES: {"lead_id", "activity_type"},
}

Chunk = list[tuple[int, dict[str, str]]]
ChunkParser = Callable[[bytes, int], Iterator[Chunk]]


def read_header(content: bytes) -> list[str]:
    text = content.decode("utf-8-sig", errors="replace")
    first = next(csv.reader(io.StringIO(text, newline="")), [])
    return [name.strip() for name in first]


def parse_chunks(content: bytes, chunk_rows: int = CHUNK_ROWS) -> Iterator[Chunk]:
    """Yield ``[(row_number, row)]`` chunks; row numbers start at 1 after the header."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig", errors="replace"), newline=""))
    chunk: Chunk = []
    for number, record in enumerate(reader, start=1):
        row = {str(k).strip(): (v if isinstance(v, str) else "") for k, v in record.items() if k}
        chunk.append((number, row))
        if len(chunk) >= chunk_rows:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def count_rows(content: bytes) -> int:
    return sum(len(chunk) for chunk in parse_chunks(content, CHUNK_ROWS))


def row_sha256(entity: ImportEntity, row: dict[str, str]) -> str:
    cleaned = {k: _blank_to_none(v) for k, v in sorted(row.items())}
    body = json.dumps({"entity": entity.value, "row": cleaned}, sort_keys=True).encode()
    return hashlib.sha256(body).hexdigest()


class Counters:
    def __init__(self) -> None:
        self.processed = 0
        self.upserted = 0
        self.duplicates = 0
        self.errors = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "upserted": self.upserted,
            "duplicates": self.duplicates,
            "errors": self.errors,
        }


class ImportTransaction(Protocol):
    @property
    def company_tx(self) -> Any: ...
    @property
    def lead_tx(self) -> Any: ...
    @property
    def action_tx(self) -> Any: ...
    async def lock_key(self, key: str) -> None: ...
    async def find_job_by_key(self, tenant_id: UUID, key: str) -> dict[str, Any] | None: ...
    async def count_active_jobs(self, tenant_id: UUID) -> int: ...
    async def insert_upload_and_job(self, row: dict[str, Any]) -> None: ...
    async def get_job(self, tenant_id: UUID | None, job_id: UUID) -> dict[str, Any] | None: ...
    async def claim_next_job(self, now: datetime) -> dict[str, Any] | None: ...
    def row_scope(self) -> AbstractAsyncContextManager[None]: ...
    async def insert_row(
        self, job: dict[str, Any], row_number: int, sha: str, payload: dict[str, Any]
    ) -> bool: ...
    async def set_row_status(self, job_id: UUID, row_number: int, status: str) -> None: ...
    async def record_error_row(
        self, job: dict[str, Any], row_number: int, sha: str, payload: dict[str, Any], error: str
    ) -> None: ...
    async def add_progress(self, job_id: UUID, counters: dict[str, int]) -> None: ...
    async def finish_job(
        self, job: dict[str, Any], *, status: str, error: str | None, now: datetime
    ) -> None: ...


class ImportUnitOfWork(Protocol):
    async def __aenter__(self) -> ImportTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class _Borrowed:
    """Lend an open sub-transaction to a writer that expects its own unit of work."""

    def __init__(self, tx: Any) -> None:
        self._tx = tx

    async def __aenter__(self) -> Any:
        return self._tx

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def job_view(job: dict[str, Any]) -> dict[str, Any]:
    def stamp(value: Any) -> Any:
        return value.isoformat() if isinstance(value, datetime) else value

    return {
        "job_id": str(job["job_id"]),
        "status": job["status"],
        "entity": job["entity"],
        "progress": {
            "total": int(job["total_rows"]),
            "processed": int(job["processed_rows"]),
            "upserted": int(job["upserted_rows"]),
            "duplicates": int(job["duplicate_rows"]),
            "errors": int(job["error_rows"]),
        },
        "started_at": stamp(job.get("started_at")),
        "finished_at": stamp(job.get("finished_at")),
        "error": job.get("error_message"),
    }


async def create_import(
    unit_of_work: ImportUnitOfWork,
    entity: ImportEntity,
    content: bytes,
    *,
    tenant_id: UUID,
    idempotency_key: str,
    filename: str = "upload.csv",
    content_type: str = "text/csv",
    preserve_blob: bool = False,
    parser: ChunkParser = parse_chunks,
    job_id: UUID | None = None,
) -> tuple[dict[str, Any], bool]:
    """Validate the upload and queue a job; return ``(job view, created)``."""
    if len(content) > MAX_BYTES:
        raise ImportRejectedError("file_too_large", 413)
    if not content.strip():
        raise ImportRejectedError("empty_file", 422)
    header = read_header(content)
    known = set(ROW_MODELS[entity].model_fields)
    if not REQUIRED_COLUMNS[entity] <= set(header) or not set(header) <= known:
        raise ImportRejectedError("invalid_header", 422)
    total = sum(len(chunk) for chunk in parser(content, CHUNK_ROWS))
    if total > MAX_ROWS:
        raise ImportRejectedError("too_many_rows", 413)
    digest = hashlib.sha256(content + entity.value.encode()).hexdigest()
    async with unit_of_work as tx:
        await tx.lock_key(f"import:{tenant_id}")
        existing = await tx.find_job_by_key(tenant_id, idempotency_key)
        if existing is not None:
            if existing["sha256"] != digest:
                raise IdempotencyKeyReuseError(idempotency_key)
            return job_view(existing), False
        if await tx.count_active_jobs(tenant_id) >= MAX_ACTIVE_JOBS:
            raise ImportRejectedError("too_many_active_jobs", 429)
        row = {
            "job_id": job_id or uuid4(),
            "upload_id": uuid4(),
            "tenant_id": tenant_id,
            "entity": entity.value,
            "idempotency_key": idempotency_key,
            "filename": filename,
            "content_type": content_type,
            "content": content,
            "sha256": digest,
            "total_rows": total,
            "preserve_blob": preserve_blob,
        }
        await tx.insert_upload_and_job(row)
        return job_view(
            {
                **row,
                "status": "queued",
                "processed_rows": 0,
                "upserted_rows": 0,
                "duplicate_rows": 0,
                "error_rows": 0,
            }
        ), True


async def read_job(
    unit_of_work: ImportUnitOfWork, job_id: UUID, tenant_id: UUID | None
) -> dict[str, Any]:
    async with unit_of_work as tx:
        job = await tx.get_job(tenant_id, job_id)
    if job is None:
        raise JobNotFoundError(str(job_id))
    return job_view(job)


async def _apply_row(
    tx: ImportTransaction, job: dict[str, Any], entity: ImportEntity, model: _Row, sha: str
) -> str:
    """Write one validated row with the existing writers; return ``upserted`` or ``duplicate``."""
    tenant_id: UUID = job["tenant_id"]
    values = model.model_dump(exclude_none=True)
    if entity is ImportEntity.COMPANIES:
        result = await upsert_company(
            _Borrowed(tx.company_tx), CompanyUpsert(**values, tenant_id=tenant_id)
        )
        return "duplicate" if result["status"] == "unchanged" else "upserted"
    if entity is ImportEntity.LEADS:
        _view, created = await create_lead(
            _Borrowed(tx.lead_tx),
            LeadCreate(**values),
            tenant_id=tenant_id,
            idempotency_key=f"import:{sha}",
        )
        return "upserted" if created else "duplicate"
    lead_id = values.pop("lead_id")
    _view, created = await log_activity(
        _Borrowed(tx.action_tx),
        lead_id,
        ActivityCreate(**values),
        tenant_id=tenant_id,
        idempotency_key=f"import:{sha}",
    )
    return "upserted" if created else "duplicate"


async def _process_chunk(
    unit_of_work: ImportUnitOfWork, job: dict[str, Any], entity: ImportEntity, chunk: Chunk
) -> Counters:
    counters = Counters()
    model_type = ROW_MODELS[entity]
    async with unit_of_work as tx:
        for number, raw in chunk:
            counters.processed += 1
            sha = row_sha256(entity, raw)
            try:
                model = model_type.model_validate(raw)
                async with tx.row_scope():
                    if not await tx.insert_row(job, number, sha, raw):
                        counters.duplicates += 1
                        continue
                    outcome = await _apply_row(tx, job, entity, model, sha)
                    await tx.set_row_status(job["job_id"], number, outcome)
            except Exception as exc:
                counters.errors += 1
                await tx.record_error_row(job, number, sha, raw, _short_error(exc))
                continue
            if outcome == "upserted":
                counters.upserted += 1
            else:
                counters.duplicates += 1
        await tx.add_progress(job["job_id"], counters.as_dict())
    return counters


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        return f"{location}: {first['msg']}"[:300]
    return f"{type(exc).__name__}: {exc}"[:300]


async def process_next_job(
    unit_of_work_factory: Callable[[], ImportUnitOfWork],
    *,
    parser: ChunkParser = parse_chunks,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str | None:
    """Claim and run one queued job; return its final status, or None when none is queued."""
    async with unit_of_work_factory() as tx:
        job = await tx.claim_next_job(now())
    if job is None:
        return None
    entity = ImportEntity(job["entity"])
    status, error = "succeeded", None
    try:
        for chunk in parser(job["content"], CHUNK_ROWS):
            await _process_chunk(unit_of_work_factory(), job, entity, chunk)
    except Exception as exc:
        status, error = "failed", _short_error(exc)
    async with unit_of_work_factory() as tx:
        await tx.finish_job(job, status=status, error=error, now=now())
    return status
