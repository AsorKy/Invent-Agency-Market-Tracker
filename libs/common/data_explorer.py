"""Serve the read-only data explorer of one service under ``/internal/<service>/data``.

The web data explorer shows the raw Bright Data tables of ``ingest`` and every table derived
from them. ``CATALOG`` names each dataset with its owning service and origin; the gateway routes
``/api/v1/data/{dataset}`` by it and lists the datasets of a service that is down. Each service
declares its own datasets in ``services/<service>/app/data_explorer.py`` (INV-01: it reads only
its own schema) with one fixed page statement and one fixed count statement each, and mounts
``data_router``.

Every caller value is bound, never interpolated: the tenant, the text filter ``:q`` (null for
none), the keyset ``:k1`` ... ``:kN`` (null on the first page) and ``:limit``. A page is ordered
by the dataset key descending; the opaque cursor is the key of the last row as base64url JSON,
like ``libs/common/pagination.py``. Each query runs in one transaction that calls
``set_tenant_context`` first. The service ``Scope`` returns the extra bind values for the tenant
(``tenant_id`` for tables that have the column), or ``None`` when the tenant owns no row of the
service. JSON cells stay JSON; a JSON column is selected as ``left(<column>::text, 20001)`` so no
page carries more than ``CELL_LIMIT`` plus one characters of it, and any cell longer than
``CELL_LIMIT`` characters becomes its first ``CELL_LIMIT`` characters plus ``TRUNCATION_MARKER``
with ``truncated_cells`` set. Relates to ``services/gateway/app/routes.py`` and ``main.py``.
"""

import base64
import binascii
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from libs.common.internal_auth import GATEWAY_SERVICE, require_service_token
from libs.common.tenant import set_tenant_context

Origin = Literal["bright_data_raw", "derived"]

DATA_SERVICES: tuple[str, ...] = ("ingest", "core", "ml", "brief", "pulse")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_QUERY_LENGTH = 200
CELL_LIMIT = 20_000
TRUNCATION_MARKER = "...[truncated]"
JSON_TYPES = frozenset({"json", "jsonb"})
# The tenant header is trusted only from the gateway, which derives it from verified claims.
GATEWAY_ONLY = Depends(require_service_token(GATEWAY_SERVICE))


@dataclass(frozen=True, slots=True)
class DatasetInfo:
    """Identity of one dataset: id, owning service, origin, and labels for the explorer."""

    id: str
    service: str
    origin: Origin
    title: str
    description: str


CATALOG: dict[str, DatasetInfo] = {
    info.id: info
    for info in (
        DatasetInfo(
            "raw_snapshots",
            "ingest",
            "bright_data_raw",
            "Raw snapshots",
            "Bright Data snapshots with the payload exactly as delivered (ingest.raw_snapshot).",
        ),
        DatasetInfo(
            "raw_records",
            "ingest",
            "bright_data_raw",
            "Raw records",
            "Every record of every Bright Data snapshot as delivered, one row per record "
            "(ingest.raw_snapshot, payload expanded).",
        ),
        DatasetInfo(
            "entity_candidates",
            "ingest",
            "derived",
            "Entity candidates",
            "Companies mapped from raw snapshots, before resolution (ingest.entity_candidate).",
        ),
        DatasetInfo(
            "rues_queue",
            "ingest",
            "derived",
            "RUES lookup queue",
            "Candidates waiting for a RUES registry lookup (ingest.rues_lookup_queue).",
        ),
        DatasetInfo(
            "failed_records",
            "ingest",
            "bright_data_raw",
            "Failed records",
            "Delivered records that could not be mapped, with their raw payload "
            "(ingest.failed_record).",
        ),
        DatasetInfo(
            "failed_fetches",
            "ingest",
            "bright_data_raw",
            "Failed fetches",
            "Bright Data fetches that failed, with retry state (ingest.failed_fetch).",
        ),
        DatasetInfo(
            "pending_snapshots",
            "ingest",
            "bright_data_raw",
            "Pending snapshots",
            "Triggered Bright Data collections waiting for delivery (ingest.pending_snapshot).",
        ),
        DatasetInfo(
            "companies",
            "core",
            "derived",
            "Companies",
            "Resolved companies of the tenant (core.company).",
        ),
        DatasetInfo(
            "signals",
            "core",
            "derived",
            "Signals",
            "Market signals of the tenant's companies, with their payload (core.signal).",
        ),
        DatasetInfo(
            "leads",
            "core",
            "derived",
            "Leads",
            "Leads of the tenant and their pipeline stage (core.lead).",
        ),
        DatasetInfo(
            "lead_scores",
            "ml",
            "derived",
            "Lead scores",
            "Current score of each of the tenant's companies, with features and weights "
            "(ml.lead_score).",
        ),
        DatasetInfo(
            "record_digests",
            "brief",
            "derived",
            "Record digests",
            "Generated company digests (brief.record_digest).",
        ),
        DatasetInfo(
            "fact_signals",
            "pulse",
            "derived",
            "Fact signals",
            "Signals projected for the market metrics (pulse.fact_signal).",
        ),
        DatasetInfo(
            "daily_rollups",
            "pulse",
            "derived",
            "Daily rollups",
            "Signal and lead counts per day, city, and service line (pulse.daily_rollup).",
        ),
    )
}


@dataclass(frozen=True, slots=True)
class Dataset:
    """A dataset served by its owning service.

    ``columns`` is the select list in order as (name, PostgreSQL type). ``key`` names the
    columns of the descending order, bound as ``:k1`` ... ``:kN``. ``hidden`` lists the table
    columns left out on purpose (hashes and other secret-looking values).
    """

    id: str
    columns: tuple[tuple[str, str], ...]
    key: tuple[str, ...]
    page: TextClause
    count: TextClause
    hidden: tuple[str, ...] = ()

    @property
    def info(self) -> DatasetInfo:
        return CATALOG[self.id]

    def column_list(self) -> list[dict[str, str]]:
        return [{"name": name, "type": kind} for name, kind in self.columns]


# Returns the extra bind values that scope the queries to ``tenant_id``, or ``None`` when the
# tenant owns no row of the service; raises ``ScopeUnavailable`` when it cannot tell.
Scope = Callable[[Request, UUID], Awaitable[dict[str, Any] | None]]


class ScopeUnavailable(Exception):
    """The service cannot tell which rows the tenant owns right now (answers 503)."""


async def tenant_column_scope(request: Request, tenant_id: UUID) -> dict[str, Any] | None:
    """Scope of tables with a ``tenant_id`` column: the statements filter ``:tenant_id``."""
    return {"tenant_id": tenant_id}


def _key_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _parse_key(kind: str, value: Any) -> Any:
    if kind == "timestamptz" and isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("naive timestamp")
        return parsed
    if kind == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if kind == "uuid" and isinstance(value, str):
        return UUID(value)
    if kind in {"bigint", "integer"} and isinstance(value, int) and not isinstance(value, bool):
        return value
    if kind == "text" and isinstance(value, str):
        return value
    raise ValueError("unsupported key value")


def encode_page_cursor(dataset: Dataset, row: Mapping[str, Any]) -> str:
    """Return the opaque cursor that continues after ``row``."""
    payload = {"dataset": dataset.id, "key": [_key_value(row[name]) for name in dataset.key]}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_page_cursor(dataset: Dataset, value: str) -> dict[str, Any]:
    """Return the ``:k1`` ... ``:kN`` binds of a cursor or raise ``ValueError``."""
    kinds = dict(dataset.columns)
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        keys = payload["key"]
        if payload["dataset"] != dataset.id or len(keys) != len(dataset.key):
            raise ValueError("cursor of another dataset")
        return {
            f"k{index}": _parse_key(kinds[name], item)
            for index, (name, item) in enumerate(zip(dataset.key, keys, strict=True), start=1)
        }
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("invalid_cursor") from exc


def first_page_keys(dataset: Dataset) -> dict[str, None]:
    return {f"k{index}": None for index in range(1, len(dataset.key) + 1)}


def _truncate(text_value: str) -> tuple[str, bool]:
    if len(text_value) > CELL_LIMIT:
        return text_value[:CELL_LIMIT] + TRUNCATION_MARKER, True
    return text_value, False


def render_cell(value: Any, kind: str) -> tuple[Any, bool]:
    """Return a JSON-ready cell and whether it was truncated."""
    if value is None:
        return None, False
    if kind in JSON_TYPES:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if len(serialized) > CELL_LIMIT:
            return _truncate(serialized)
        return json.loads(serialized), False
    if isinstance(value, datetime | date):
        return value.isoformat(), False
    if isinstance(value, UUID):
        return str(value), False
    if isinstance(value, Decimal):
        return (float(value) if value.is_finite() else str(value)), False
    if isinstance(value, float) and not math.isfinite(value):
        return str(value), False
    if isinstance(value, str):
        return _truncate(value)
    return value, False


def render_row(dataset: Dataset, row: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return the row as ``{column: cell}`` in column order and whether a cell was truncated."""
    cells: dict[str, Any] = {}
    truncated = False
    for name, kind in dataset.columns:
        cells[name], cut = render_cell(row[name], kind)
        truncated = truncated or cut
    return cells, truncated


def _engine(request: Request) -> AsyncEngine | None:
    engine: AsyncEngine | None = getattr(request.app.state, "data_engine", None)
    return engine


async def read_counts(
    request: Request, datasets: Sequence[Dataset], scope: Scope, tenant_id: UUID | None
) -> dict[str, int]:
    """Row counts of the tenant per dataset; empty when the counts cannot be read."""
    engine = _engine(request)
    if tenant_id is None or engine is None:
        return {}
    try:
        bound = await scope(request, tenant_id)
    except ScopeUnavailable:
        return {}
    if bound is None:
        return {dataset.id: 0 for dataset in datasets}
    counts: dict[str, int] = {}
    try:
        async with engine.begin() as connection:
            await set_tenant_context(connection, tenant_id)
            for dataset in datasets:
                result = await connection.execute(dataset.count, {**bound, "q": None})
                counts[dataset.id] = int(result.scalar_one())
    except (SQLAlchemyError, OSError):
        return {}
    return counts


async def read_page(
    request: Request,
    dataset: Dataset,
    scope: Scope,
    tenant_id: UUID,
    *,
    limit: int,
    keys: Mapping[str, Any],
    q: str | None,
) -> dict[str, Any]:
    """Return one page of ``dataset`` for ``tenant_id``, newest first."""
    engine = _engine(request)
    if engine is None:
        raise HTTPException(status_code=503, detail="database_unavailable")
    try:
        bound = await scope(request, tenant_id)
    except ScopeUnavailable as exc:
        raise HTTPException(status_code=503, detail="scope_unavailable") from exc
    body: dict[str, Any] = {
        "dataset": dataset.id,
        "columns": dataset.column_list(),
        "data": [],
        "next_cursor": None,
        "total": 0,
        "truncated_cells": False,
    }
    if bound is None:
        return body
    params = {**bound, "q": q}
    try:
        async with engine.begin() as connection:
            await set_tenant_context(connection, tenant_id)
            total = (await connection.execute(dataset.count, params)).scalar_one()
            result = await connection.execute(dataset.page, {**params, **keys, "limit": limit + 1})
            rows = [dict(row) for row in result.mappings().all()]
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    has_more = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        cells, cut = render_row(dataset, row)
        body["data"].append(cells)
        body["truncated_cells"] = body["truncated_cells"] or cut
    body["total"] = int(total)
    if has_more and rows:
        body["next_cursor"] = encode_page_cursor(dataset, rows[-1])
    return body


def data_router(
    service: str, datasets: Sequence[Dataset], scope: Scope = tenant_column_scope
) -> APIRouter:
    """Return the catalog and page routes of ``service``; every dataset must be its own."""
    foreign = [dataset.id for dataset in datasets if CATALOG[dataset.id].service != service]
    if foreign:
        raise ValueError(f"{service} does not own {foreign}")
    by_id = {dataset.id: dataset for dataset in datasets}
    router = APIRouter(prefix=f"/internal/{service}/data")

    @router.get("/catalog", dependencies=[GATEWAY_ONLY])
    async def data_catalog(
        request: Request, x_tenant_id: Annotated[UUID | None, Header()] = None
    ) -> dict[str, Any]:
        counts = await read_counts(request, datasets, scope, x_tenant_id)
        return {
            "datasets": [
                {
                    "id": dataset.id,
                    "title": dataset.info.title,
                    "description": dataset.info.description,
                    "service": service,
                    "origin": dataset.info.origin,
                    "count": counts.get(dataset.id),
                    "columns": dataset.column_list(),
                }
                for dataset in datasets
            ]
        }

    @router.get("/{dataset}", dependencies=[GATEWAY_ONLY])
    async def data_page(
        dataset: str,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        cursor: str | None = None,
        q: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
        x_tenant_id: Annotated[UUID | None, Header()] = None,
    ) -> dict[str, Any]:
        spec = by_id.get(dataset)
        if spec is None:
            raise HTTPException(status_code=404, detail="dataset_not_found")
        if x_tenant_id is None:
            raise HTTPException(status_code=400, detail="tenant_required")
        try:
            keys = decode_page_cursor(spec, cursor) if cursor else first_page_keys(spec)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_cursor") from exc
        text_filter = (q or "").strip() or None
        return await read_page(
            request, spec, scope, x_tenant_id, limit=limit, keys=keys, q=text_filter
        )

    return router
