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
from uuid import UUID, uuid4

from sqlalchemy import text

from libs.common.db import create_engine
from libs.common.pagination import decode_cursor, encode_cursor
from libs.contracts.api import SignalCreate


class CoreRepository:
    def __init__(self, database_url: str | None) -> None:
        self.engine = create_engine(database_url, pool_size=4)

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
