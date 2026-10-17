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


class IngestRepository:
    def __init__(self, database_url: str | None, redis_url: str) -> None:
        self.engine = create_engine(database_url, pool_size=2)
        self.redis: Redis = create_redis(redis_url)

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
            row_id = result.scalar_one_or_none()
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
        claimed = await self.redis.set(queue_key, "1", nx=True, ex=86_400)
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
                text("SELECT payload FROM ingest.raw_snapshot WHERE id = :id"),
                {"id": raw_snapshot_id},
            )
            row = result.mappings().first()
        if row is None:
            raise ValueError(f"raw snapshot not found: {raw_snapshot_id}")

        raw_payload = row["payload"]
        records = raw_payload if isinstance(raw_payload, list) else [raw_payload]
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
                candidate_key = hashlib.sha256(
                    f"{normalized_name}|{city or ''}|{nit or ''}".encode()
                ).hexdigest()
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
                        "candidate_key": candidate_key,
                        "name": name,
                        "city": city if isinstance(city, str) else None,
                        "candidate_nit": nit if isinstance(nit, str) else None,
                        "normalized_name": normalized_name,
                    },
                )
                created += result.rowcount or 0
        return created
