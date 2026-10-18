"""Create or update a company with SCD2 history and a transactional outbox event.

``upsert_company`` resolves the company by NIT or by canonical key (normalized name, city, source
IDs), compares the tracked attributes, and only when something changed it closes the previous
version into history, bumps ``version``, and writes the ``company.updated`` event, all inside one
``CompanyUnitOfWork`` transaction. It relates to ``main.py`` (route), ``repository.py`` (SQL unit
of work), and ``libs/contracts/events.py``. Repeating the same payload writes nothing, which keeps
ingest retries idempotent (REQ-COR-007). Complexity is O(fields) per call.
"""

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

TRACKED_FIELDS = (
    "nit",
    "legal_name",
    "trade_name",
    "sector_code",
    "employee_count",
    "website",
    "city",
    "source_ids",
)
EVENT_TYPE = "company.updated"


class CompanyUpsert(BaseModel):
    """Request body; omitted optional fields keep their stored value."""

    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=1, max_length=300)
    nit: str | None = Field(default=None, max_length=32)
    trade_name: str | None = Field(default=None, max_length=300)
    sector_code: str | None = Field(default=None, max_length=16)
    employee_count: int | None = Field(default=None, ge=0)
    website: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=120)
    source_ids: dict[str, str] = Field(default_factory=dict)
    tenant_id: UUID | None = None


class CompanyTransaction(Protocol):
    """Operations that must share one database transaction."""

    async def lock_key(self, key: str) -> None: ...
    async def find_company(self, nit: str | None, slug: str) -> dict[str, Any] | None: ...
    async def insert_company(self, row: dict[str, Any]) -> None: ...
    async def close_version(self, old: dict[str, Any], closed_at: datetime) -> None: ...
    async def update_company(self, row: dict[str, Any]) -> None: ...
    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_id: UUID,
        version: int,
        payload: dict[str, Any],
    ) -> None: ...


class CompanyUnitOfWork(Protocol):
    """Async context manager: commit on clean exit, roll back on error."""

    async def __aenter__(self) -> CompanyTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


def normalize_nit(nit: str | None) -> str | None:
    """Keep only alphanumerics so ``900.123.456-7`` and ``9001234567`` match."""
    if nit is None:
        return None
    cleaned = re.sub(r"[^0-9A-Za-z]", "", nit).upper()
    return cleaned or None


def _fold(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().lower()


def company_slug(payload: CompanyUpsert) -> str:
    """Return the deterministic identity key: NIT when present, else the canonical key."""
    nit = normalize_nit(payload.nit)
    if nit:
        return f"nit-{nit.lower()}"
    canonical = "|".join(
        [
            _fold(payload.legal_name),
            _fold(payload.city),
            ";".join(f"{k}={v}" for k, v in sorted(payload.source_ids.items())),
        ]
    )
    return "ck-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


def _current_values(row: dict[str, Any]) -> dict[str, Any]:
    flags = row.get("source_flags") or {}
    values = {name: row.get(name) for name in TRACKED_FIELDS if name != "source_ids"}
    values["source_ids"] = dict(flags.get("source_ids", {}))
    return values


def _merge(current: dict[str, Any], payload: CompanyUpsert) -> dict[str, Any]:
    merged = dict(current)
    incoming = payload.model_dump(exclude={"tenant_id"})
    incoming["nit"] = normalize_nit(payload.nit)
    for name in TRACKED_FIELDS:
        if name == "source_ids":
            merged[name] = {**current[name], **payload.source_ids}
        elif name == "legal_name" or incoming[name] is not None:
            merged[name] = incoming[name]
    return merged


def _event_payload(
    row: dict[str, Any], change: str, changed: list[str], request_id: str | None
) -> dict[str, Any]:
    return {
        "company_id": str(row["id"]),
        "slug": row["slug"],
        "change": change,
        "changed_fields": changed,
        "version": row["version"],
        "request_id": request_id,
        **{name: row[name] for name in TRACKED_FIELDS if name != "source_ids"},
        "source_ids": row["source_flags"]["source_ids"],
    }


async def upsert_company(
    unit_of_work: CompanyUnitOfWork,
    payload: CompanyUpsert,
    *,
    request_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply the upsert; return ``status`` (created, updated, unchanged), id, and version."""
    moment = now or datetime.now(UTC)
    slug = company_slug(payload)
    nit = normalize_nit(payload.nit)
    async with unit_of_work as tx:
        await tx.lock_key(slug)
        existing = await tx.find_company(nit, slug)
        if existing is None:
            row = {
                "id": uuid4(),
                "tenant_id": payload.tenant_id,
                "slug": slug,
                "version": 1,
                "valid_from": moment,
                "updated_at": moment,
                **_merge(
                    {
                        "nit": None,
                        "legal_name": payload.legal_name,
                        "trade_name": None,
                        "sector_code": None,
                        "employee_count": None,
                        "website": None,
                        "city": None,
                        "source_ids": {},
                    },
                    payload,
                ),
            }
            row["source_flags"] = {"source_ids": row.pop("source_ids")}
            await tx.insert_company(row)
            changed = [n for n in TRACKED_FIELDS if n != "source_ids" and row[n] is not None]
            await tx.add_outbox_event(
                event_id=uuid4(),
                tenant_id=row["tenant_id"],
                aggregate_id=row["id"],
                version=1,
                payload=_event_payload(row, "created", changed, request_id),
            )
            return {"status": "created", "company_id": row["id"], "version": 1}

        current = _current_values(existing)
        merged = _merge(current, payload)
        changed = [name for name in TRACKED_FIELDS if merged[name] != current[name]]
        if not changed:
            return {
                "status": "unchanged",
                "company_id": existing["id"],
                "version": existing["version"],
            }
        await tx.close_version(existing, moment)
        flags = {**(existing.get("source_flags") or {}), "source_ids": merged.pop("source_ids")}
        row = {
            **existing,
            **merged,
            "source_flags": flags,
            "version": existing["version"] + 1,
            "valid_from": moment,
            "updated_at": moment,
        }
        await tx.update_company(row)
        await tx.add_outbox_event(
            event_id=uuid4(),
            tenant_id=row.get("tenant_id"),
            aggregate_id=row["id"],
            version=row["version"],
            payload=_event_payload(row, "updated", changed, request_id),
        )
        return {"status": "updated", "company_id": row["id"], "version": row["version"]}
