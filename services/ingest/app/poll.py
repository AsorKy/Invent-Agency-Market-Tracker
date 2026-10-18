"""Track triggered collections and poll them when no webhook arrives.

Every trigger is recorded in ``ingest.pending_snapshot`` (REQ-ING-008). While
``INGEST_POLL_ENABLED`` is true, ``SnapshotPoller`` asks the connector for the
progress of each pending snapshot and stores ready ones through the same
``persist_snapshot`` path as the webhook. A snapshot already delivered by the
webhook is detected by its id and only marked ``delivered``, so it is stored
once (REQ-ING-001); the unique key on ``raw_snapshot`` guards the remaining
race. Statuses: ``pending``, ``stored`` (by polling), ``delivered`` (by
webhook), ``failed``. Connector calls are synchronous and run in a thread. It
relates to ``connectors.py``, ``source_config.py``, ``repository.py``, and
``main.py``.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from services.ingest.app.connectors import ConnectorError, SourceConnector
from services.ingest.app.snapshot import payload_sha256

logger = logging.getLogger(__name__)

MAX_POLL_ERRORS = 10

PersistSnapshot = Callable[..., Awaitable[tuple[UUID, bool, bool]]]


@dataclass(frozen=True)
class PendingSnapshot:
    """One tracked collection waiting for delivery."""

    id: UUID
    source: str
    snapshot_id: str
    attempts: int


class PendingSnapshotRepository:
    """SQL access for ``ingest.pending_snapshot``; the engine is injected."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    async def track(self, source: str, snapshot_id: str, inputs: list[dict[str, Any]]) -> None:
        """Record a trigger; repeating the same trigger is a no-op."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ingest.pending_snapshot
                        (snapshot_id, source, payload, payload_sha256)
                    VALUES (:snapshot_id, :source, CAST(:payload AS jsonb), :payload_sha256)
                    ON CONFLICT (source, snapshot_id, payload_sha256) DO NOTHING
                    """
                ),
                {
                    "snapshot_id": snapshot_id,
                    "source": source,
                    "payload": json.dumps(inputs),
                    "payload_sha256": payload_sha256(inputs),
                },
            )

    async def list_pending(self, limit: int = 50) -> list[PendingSnapshot]:
        """Return the oldest snapshots still waiting."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, source, snapshot_id, attempts FROM ingest.pending_snapshot
                    WHERE status = 'pending' ORDER BY created_at LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
            return [PendingSnapshot(**dict(row)) for row in result.mappings().all()]

    async def already_stored(self, snapshot_id: str) -> bool:
        """Return True when ``raw_snapshot`` holds the snapshot (webhook won)."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT 1 FROM ingest.raw_snapshot WHERE source = 'bright_data' "
                    "AND snapshot_id = :snapshot_id LIMIT 1"
                ),
                {"snapshot_id": snapshot_id},
            )
            return result.first() is not None

    async def set_status(self, pending_id: UUID, status: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text("UPDATE ingest.pending_snapshot SET status = :status WHERE id = :id"),
                {"id": pending_id, "status": status},
            )

    async def record_error(self, pending_id: UUID, *, give_up: bool) -> None:
        """Count a failed poll attempt; mark the snapshot failed when giving up."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE ingest.pending_snapshot
                    SET attempts = attempts + 1,
                        status = CASE WHEN :give_up THEN 'failed' ELSE status END
                    WHERE id = :id
                    """
                ),
                {"id": pending_id, "give_up": give_up},
            )


async def trigger_and_track(
    connector: SourceConnector,
    pending: PendingSnapshotRepository,
    inputs: list[dict[str, Any]],
    *,
    notify_url: str | None,
) -> str:
    """Trigger a collection (never retried) and track it; return the snapshot id."""
    result = await asyncio.to_thread(connector.trigger, inputs, notify_url=notify_url)
    await pending.track(connector.source, result.snapshot_id, inputs)
    return result.snapshot_id


class SnapshotPoller:
    """Poll pending snapshots and store the ready ones once."""

    def __init__(
        self,
        pending: PendingSnapshotRepository,
        connectors: dict[str, SourceConnector],
        persist_snapshot: PersistSnapshot,
        *,
        max_errors: int = MAX_POLL_ERRORS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.pending = pending
        self.connectors = connectors
        self.persist_snapshot = persist_snapshot
        self.max_errors = max_errors
        self.clock = clock

    async def poll_once(self) -> dict[str, int]:
        """Process every pending snapshot once and return a count per outcome."""
        counts = {"stored": 0, "delivered": 0, "waiting": 0, "failed": 0}
        for item in await self.pending.list_pending():
            outcome = await self._poll_item(item)
            counts[outcome] += 1
        return counts

    async def _poll_item(self, item: PendingSnapshot) -> str:
        if await self.pending.already_stored(item.snapshot_id):
            await self.pending.set_status(item.id, "delivered")
            return "delivered"
        connector = self.connectors.get(item.source)
        if connector is None:
            await self.pending.set_status(item.id, "failed")
            return "failed"
        try:
            progress = await asyncio.to_thread(connector.progress, item.snapshot_id)
            if progress.status == "failed":
                await self.pending.set_status(item.id, "failed")
                return "failed"
            if progress.status != "ready":
                return "waiting"
            records = await asyncio.to_thread(connector.fetch, item.snapshot_id)
        except ConnectorError as exc:
            give_up = (not exc.retryable) or item.attempts + 1 >= self.max_errors
            logger.warning(
                "poll error source=%s snapshot=%s retryable=%s",
                item.source,
                item.snapshot_id,
                exc.retryable,
            )
            await self.pending.record_error(item.id, give_up=give_up)
            return "failed" if give_up else "waiting"
        # Re-check just before writing: the webhook may have won while we downloaded.
        if await self.pending.already_stored(item.snapshot_id):
            await self.pending.set_status(item.id, "delivered")
            return "delivered"
        await self.persist_snapshot(
            snapshot_id=item.snapshot_id,
            dataset_id=getattr(connector, "dataset_id", item.source),
            payload=records,
            payload_sha256=payload_sha256(records),
            fetched_at=self.clock(),
        )
        await self.pending.set_status(item.id, "stored")
        return "stored"

    async def run_forever(self, interval_seconds: float) -> None:
        """Poll until cancelled; one failing cycle never stops the loop."""
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("poll cycle failed")
            await asyncio.sleep(interval_seconds)
