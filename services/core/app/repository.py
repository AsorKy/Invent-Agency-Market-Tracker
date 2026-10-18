"""Persist signals and basic reads for the Core service.

``create_signal`` generates a deterministic external key when the source does
not provide one and writes the signal plus outbox record in one transaction;
list methods query company and lead projections. It relates to ``main.py``,
``outbox_relay.py``, and ``libs/contracts/api.py``. It preserves idempotency on
ingest retries and turns accepted changes into events.
"""

import hashlib
import json
from datetime import datetime
from types import TracebackType
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from libs.common.db import create_engine
from libs.common.pagination import decode_cursor, encode_cursor
from libs.contracts.api import SignalCreate


class SqlCompanyTransaction:
    """Company writes bound to one open connection (one transaction)."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def lock_key(self, key: str) -> None:
        await self.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"company:{key}"}
        )

    async def find_company(self, nit: str | None, slug: str) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT id, tenant_id, nit, slug, legal_name, trade_name, sector_code,
                       employee_count, website, city, source_flags, version, valid_from
                FROM core.company
                WHERE (CAST(:nit AS text) IS NOT NULL AND nit = :nit) OR slug = :slug
                ORDER BY (nit = :nit) DESC NULLS LAST
                LIMIT 1
                FOR UPDATE
                """
            ),
            {"nit": nit, "slug": slug},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def insert_company(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.company
                    (id, tenant_id, nit, slug, legal_name, trade_name, sector_code,
                     employee_count, website, city, source_flags, version, valid_from,
                     created_at, updated_at)
                VALUES
                    (:id, :tenant_id, :nit, :slug, :legal_name, :trade_name, :sector_code,
                     :employee_count, :website, :city, CAST(:source_flags AS jsonb), :version,
                     :valid_from, :updated_at, :updated_at)
                """
            ),
            {**row, "source_flags": json.dumps(row["source_flags"])},
        )

    async def close_version(self, old: dict[str, object], closed_at: datetime) -> None:
        """Append the superseded version to history (SCD2 row with valid_from/valid_to)."""
        snapshot = {k: v for k, v in old.items() if k not in {"id", "valid_from"}}
        await self.connection.execute(
            text(
                """
                INSERT INTO core.audit_log (actor, action, aggregate_type, aggregate_id, payload)
                VALUES ('core', 'company.version_closed', 'company', :id, CAST(:payload AS jsonb))
                """
            ),
            {
                "id": old["id"],
                "payload": json.dumps(
                    snapshot
                    | {
                        "valid_from": str(old["valid_from"]),
                        "valid_to": closed_at.isoformat(),
                    },
                    default=str,
                ),
            },
        )

    async def update_company(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                UPDATE core.company SET
                    nit = :nit, legal_name = :legal_name, trade_name = :trade_name,
                    sector_code = :sector_code, employee_count = :employee_count,
                    website = :website, city = :city,
                    source_flags = CAST(:source_flags AS jsonb), version = :version,
                    valid_from = :valid_from, updated_at = :updated_at
                WHERE id = :id
                """
            ),
            {**row, "source_flags": json.dumps(row["source_flags"])},
        )

    async def add_outbox_event(
        self, *, event_id: UUID, aggregate_id: UUID, version: int, payload: dict[str, object]
    ) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.outbox_event
                    (event_id, aggregate_type, aggregate_id, event_type, version, payload)
                VALUES
                    (:event_id, 'company', :aggregate_id, 'company.updated', :version,
                     CAST(:payload AS jsonb))
                """
            ),
            {
                "event_id": event_id,
                "aggregate_id": aggregate_id,
                "version": version,
                "payload": json.dumps(payload, default=str),
            },
        )


class SqlCompanyUnitOfWork:
    """Open ``engine.begin()``: commit on success, roll back on any error."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._context = engine.begin()

    async def __aenter__(self) -> SqlCompanyTransaction:
        return SqlCompanyTransaction(await self._context.__aenter__())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._context.__aexit__(exc_type, exc, tb)


class CoreRepository:
    def __init__(self, database_url: str | None) -> None:
        self.engine = create_engine(database_url, pool_size=4)

    def company_unit_of_work(self) -> SqlCompanyUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        return SqlCompanyUnitOfWork(self.engine)

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()

    async def create_signal(
        self, signal: SignalCreate, *, request_id: str | None
    ) -> tuple[UUID, UUID | None]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        signal_id = uuid4()
        event_id = uuid4()
        source_external_id = signal.source_external_id or self._derived_external_id(signal)
        payload = signal.model_dump(mode="json") | {
            "signal_id": str(signal_id),
            "source_external_id": source_external_id,
        }
        async with self.engine.begin() as connection:
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO core.signal
                        (id, company_id, source, source_external_id, signal_type,
                         observed_at, strength, payload)
                    VALUES
                        (:id, :company_id, :source, :source_external_id, :signal_type,
                         :observed_at, :strength, CAST(:payload AS jsonb))
                    ON CONFLICT (source, source_external_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": signal_id,
                    "company_id": signal.company_id,
                    "source": signal.source,
                    "source_external_id": source_external_id,
                    "signal_type": signal.signal_type,
                    "observed_at": signal.observed_at,
                    "strength": signal.strength,
                    "payload": json.dumps(signal.payload),
                },
            )
            inserted_signal_id = inserted.scalar_one_or_none()
            if inserted_signal_id is None:
                existing = await connection.execute(
                    text(
                        """
                        SELECT id FROM core.signal
                        WHERE source = :source AND source_external_id = :source_external_id
                        """
                    ),
                    {
                        "source": signal.source,
                        "source_external_id": source_external_id,
                    },
                )
                existing_signal_id: UUID = existing.scalar_one()
                return existing_signal_id, None
            await connection.execute(
                text(
                    """
                    INSERT INTO core.outbox_event
                        (event_id, aggregate_type, aggregate_id, event_type, version, payload)
                    VALUES
                        (:event_id, 'signal', :aggregate_id, 'signal.created', 1,
                         CAST(:payload AS jsonb))
                    """
                ),
                {
                    "event_id": event_id,
                    "aggregate_id": signal_id,
                    "payload": json.dumps(payload | {"request_id": request_id}),
                },
            )
        return inserted_signal_id, event_id

    @staticmethod
    def _derived_external_id(signal: SignalCreate) -> str:
        fingerprint = json.dumps(
            {
                "company_id": str(signal.company_id),
                "source": signal.source,
                "signal_type": signal.signal_type,
                "observed_at": signal.observed_at.isoformat(),
                "strength": signal.strength,
                "payload": signal.payload,
            },
            sort_keys=True,
            default=str,
        ).encode()
        return hashlib.sha256(fingerprint).hexdigest()

    async def list_companies(
        self, limit: int, cursor: str | None = None
    ) -> tuple[list[dict[str, object]], str | None]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core reads")
        page_cursor = decode_cursor(cursor) if cursor else None
        cursor_filter = ""
        parameters: dict[str, object] = {"limit": limit + 1}
        if page_cursor:
            cursor_filter = "WHERE (updated_at, id) < (:cursor_updated_at, :cursor_id)"
            parameters.update(
                {"cursor_updated_at": page_cursor.updated_at, "cursor_id": page_cursor.item_id}
            )
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT id, slug, legal_name, trade_name, sector_code, employee_count,
                           website, city, version, updated_at
                    FROM core.company
                    {cursor_filter}
                    ORDER BY updated_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                parameters,
            )
            rows = [dict(row) for row in result.mappings().all()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            updated_at, item_id = last["updated_at"], last["id"]
            if not isinstance(updated_at, datetime) or not isinstance(item_id, UUID):
                raise RuntimeError("database returned invalid company cursor fields")
            next_cursor = encode_cursor(updated_at, item_id)
        return rows, next_cursor

    async def list_leads(
        self, limit: int, cursor: str | None = None
    ) -> tuple[list[dict[str, object]], str | None]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core reads")
        page_cursor = decode_cursor(cursor) if cursor else None
        cursor_filter = ""
        parameters: dict[str, object] = {"limit": limit + 1}
        if page_cursor:
            cursor_filter = "WHERE (l.updated_at, l.id) < (:cursor_updated_at, :cursor_id)"
            parameters.update(
                {"cursor_updated_at": page_cursor.updated_at, "cursor_id": page_cursor.item_id}
            )
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT l.id, l.company_id, c.legal_name, l.service_line, l.stage,
                           l.created_at, l.updated_at
                    FROM core.lead l
                    JOIN core.company c ON c.id = l.company_id
                    {cursor_filter}
                    ORDER BY l.updated_at DESC, l.id DESC
                    LIMIT :limit
                    """
                ),
                parameters,
            )
            rows = [dict(row) for row in result.mappings().all()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            updated_at, item_id = last["updated_at"], last["id"]
            if not isinstance(updated_at, datetime) or not isinstance(item_id, UUID):
                raise RuntimeError("database returned invalid lead cursor fields")
            next_cursor = encode_cursor(updated_at, item_id)
        return rows, next_cursor
