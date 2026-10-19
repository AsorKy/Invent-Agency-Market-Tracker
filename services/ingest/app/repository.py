"""Persist snapshots and create ingest entity candidates.

The repository deduplicates by source, snapshot, and hash; it uses a Redis mark
to avoid duplicate enqueues and transforms each record into a candidate with a
deterministic key and normalized name. It relates to ``main.py``,
``processor.py``, ``libs/domain/entity_resolution.py``, and the ``ingest``
schema tables. It separates reception, retries, and RUES resolution.
"""

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text

from libs.common.db import create_engine
from libs.common.redis_client import create_redis
from libs.domain.entity_resolution import normalize_company_name
from services.ingest.app.snapshot import changed_records
from services.ingest.app.to_core import (
    RecordOutcome,
    Registered,
    SnapshotFlow,
    SqlResolutionStore,
)


def candidate_key(normalized_name: str, city: str | None, nit: str | None) -> str:
    """Return the deterministic sha256 key of a candidate (name, city, NIT)."""
    fingerprint = json.dumps(
        {"name": normalized_name, "city": city, "nit": nit},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(fingerprint).hexdigest()


class IngestRepository:
    # Optional REQ-ING-009 flow; when None, processing only creates candidates.
    flow: SnapshotFlow | None = None
    resolution_store: SqlResolutionStore | None = None

    def __init__(
        self,
        database_url: str | None,
        redis_url: str,
        *,
        enqueue_mark_ttl_seconds: int = 86_400,
    ) -> None:
        if enqueue_mark_ttl_seconds < 1:
            raise ValueError("enqueue mark TTL must be greater than zero")
        self.engine = create_engine(database_url, pool_size=2)
        self.redis: Redis = create_redis(redis_url)
        self.enqueue_mark_ttl_seconds = enqueue_mark_ttl_seconds

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()
        await self.redis.aclose()

    async def persist_snapshot(
        self,
        *,
        snapshot_id: str,
        dataset_id: str,
        payload: dict[str, Any] | list[Any],
        payload_sha256: str,
        fetched_at: datetime,
    ) -> tuple[UUID, bool, bool]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for ingest writes")
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO ingest.raw_snapshot
                        (snapshot_id, dataset_id, source, payload, payload_sha256, fetched_at)
                    VALUES (:snapshot_id, :dataset_id, 'bright_data', CAST(:payload AS jsonb),
                            :payload_sha256, :fetched_at)
                    ON CONFLICT (source, snapshot_id, payload_sha256) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "snapshot_id": snapshot_id,
                    "dataset_id": dataset_id,
                    "payload": json.dumps(payload),
                    "payload_sha256": payload_sha256,
                    "fetched_at": fetched_at,
                },
            )
            row_id: Any = result.scalar_one_or_none()
            created = row_id is not None
            if row_id is None:
                existing = await connection.execute(
                    text(
                        """
                        SELECT id FROM ingest.raw_snapshot
                        WHERE source = 'bright_data' AND snapshot_id = :snapshot_id
                          AND payload_sha256 = :payload_sha256
                        """
                    ),
                    {"snapshot_id": snapshot_id, "payload_sha256": payload_sha256},
                )
                row_id = existing.scalar_one()

        queued = await self._enqueue_snapshot(
            row_id=row_id, dataset_id=dataset_id, snapshot_id=snapshot_id
        )
        return row_id, created, queued

    async def _enqueue_snapshot(self, *, row_id: UUID, dataset_id: str, snapshot_id: str) -> bool:
        queue_key = f"invent:ingest:queued:{row_id}"
        claimed = await self.redis.set(
            queue_key,
            "1",
            nx=True,
            ex=self.enqueue_mark_ttl_seconds,
        )
        if not claimed:
            return False
        try:
            await self.redis.xadd(
                "invent.ingest",
                {
                    "raw_snapshot_id": str(row_id),
                    "dataset_id": dataset_id,
                    "snapshot_id": snapshot_id,
                },
            )
        except Exception:
            await self.redis.delete(queue_key)
            raise
        return True

    async def process_snapshot(self, raw_snapshot_id: UUID) -> int:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for ingest processing")
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT payload, dataset_id, snapshot_id "
                    "FROM ingest.raw_snapshot WHERE id = :id"
                ),
                {"id": raw_snapshot_id},
            )
            row = result.mappings().first()
            if row is None:
                raise ValueError(f"raw snapshot not found: {raw_snapshot_id}")
            previous = await connection.execute(
                text(
                    """
                    SELECT prev.payload FROM ingest.raw_snapshot AS cur
                    JOIN ingest.raw_snapshot AS prev
                      ON prev.source = cur.source AND prev.snapshot_id = cur.snapshot_id
                     AND (prev.created_at, prev.id) < (cur.created_at, cur.id)
                    WHERE cur.id = :id
                    ORDER BY prev.created_at DESC, prev.id DESC
                    LIMIT 1
                    """
                ),
                {"id": raw_snapshot_id},
            )
            previous_row = previous.mappings().first()

        # Only new or changed records may create candidates, and so new signals.
        records = changed_records(previous_row["payload"] if previous_row else None, row["payload"])
        if self.flow is not None and self.resolution_store is not None:
            return await self._deliver_records(
                raw_snapshot_id, row["dataset_id"], row["snapshot_id"], records
            )
        created = 0
        async with self.engine.begin() as connection:
            for record in records:
                if not isinstance(record, dict):
                    continue
                name = record.get("name") or record.get("business_name") or record.get("title")
                if not isinstance(name, str) or not name.strip():
                    continue
                city = record.get("city")
                nit = record.get("nit")
                normalized_name = normalize_company_name(name)
                candidate_city = city if isinstance(city, str) else None
                candidate_nit = nit if isinstance(nit, str) else None
                candidate_key_value = candidate_key(normalized_name, candidate_city, candidate_nit)
                result = await connection.execute(
                    text(
                        """
                        INSERT INTO ingest.entity_candidate
                            (raw_snapshot_id, candidate_key, name, city, candidate_nit,
                             normalized_name)
                        VALUES (:raw_snapshot_id, :candidate_key, :name, :city, :candidate_nit,
                                :normalized_name)
                        ON CONFLICT (raw_snapshot_id, candidate_key) DO NOTHING
                        """
                    ),
                    {
                        "raw_snapshot_id": raw_snapshot_id,
                        "candidate_key": candidate_key_value,
                        "name": name,
                        "city": candidate_city,
                        "candidate_nit": candidate_nit,
                        "normalized_name": normalized_name,
                    },
                )
                created += result.rowcount or 0
        return created

    async def _deliver_records(
        self,
        raw_snapshot_id: UUID,
        dataset_id: str,
        snapshot_id: str,
        records: list[Any],
    ) -> int:
        """Run each record through ``SnapshotFlow``; a failing record never stops the batch."""
        flow, store, engine = self.flow, self.resolution_store, self.engine
        if flow is None or store is None or engine is None:
            raise RuntimeError("the core flow is not configured")
        source = flow.source_for_dataset(dataset_id)
        if source is None:
            await flow.failures.record(
                source="unknown",
                snapshot_id=snapshot_id,
                record_ref=None,
                reason=f"no mapping source for dataset {dataset_id}",
                payload={"dataset_id": dataset_id},
            )
            return 0
        registered_count = 0
        for record in records:

            async def register(name: str, city: str | None, nit: str | None) -> Registered:
                # Own short transaction: the candidate row is committed at once.
                async with engine.begin() as connection:
                    return await self._register_candidate(
                        connection, raw_snapshot_id, name, city, nit
                    )

            prepared = await flow.prepare_record(
                source=source, snapshot_id=snapshot_id, record=record, register=register
            )
            # Commit the resolution writes before core is called; no connection is
            # held while the network call runs (REQ-ING-009).
            async with engine.begin() as connection:
                await store.flush(connection)
            outcome = (
                prepared
                if isinstance(prepared, RecordOutcome)
                else await flow.deliver_prepared(prepared)
            )
            if outcome.status != "failed":
                registered_count += 1
        return registered_count

    @staticmethod
    async def _register_candidate(
        connection: Any, raw_snapshot_id: UUID, name: str, city: str | None, nit: str | None
    ) -> Registered:
        normalized_name = normalize_company_name(name)
        result = await connection.execute(
            text(
                """
                INSERT INTO ingest.entity_candidate
                    (raw_snapshot_id, candidate_key, name, city, candidate_nit,
                     normalized_name)
                VALUES (:raw_snapshot_id, :candidate_key, :name, :city,
                        :candidate_nit, :normalized_name)
                ON CONFLICT (raw_snapshot_id, candidate_key)
                DO UPDATE SET name = EXCLUDED.name
                RETURNING id, resolution_attempts, status
                """
            ),
            {
                "raw_snapshot_id": raw_snapshot_id,
                "candidate_key": candidate_key(normalized_name, city, nit),
                "name": name,
                "city": city,
                "candidate_nit": nit,
                "normalized_name": normalized_name,
            },
        )
        row = result.mappings().one()
        return Registered(str(row["id"]), int(row["resolution_attempts"]), row["status"])
