"""Retry the RUES lookups queued in ``ingest.rues_lookup_queue`` (REQ-ING-003).

``RuesQueueWorker.run_once`` reads the due queue rows in a short read transaction,
resolves each candidate again through the injected ``RuesLookup`` (a mock until a
live client exists) with ``EntityResolver``, commits the resolution writes in a
second short transaction, and only then sends a resolved candidate to core through
``SnapshotFlow.deliver_company``. No database connection is open during the core
call. The resolver sets the retry time (``RETRY_BACKOFF``) and quarantines a
candidate after two attempts. If core fails, the candidate is queued again with the
error so the next pass retries it. It relates to ``to_core.py`` and
``libs/domain/entity_resolution.py``.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from libs.domain.entity_resolution import (
    RETRY_BACKOFF,
    Candidate,
    EntityResolver,
    RuesLookup,
)
from services.ingest.app.to_core import SnapshotFlow, SqlResolutionStore

LOGGER = logging.getLogger(__name__)
DELIVER_DECISIONS = frozenset({"matched"})
ERROR_LIMIT = 200

DUE_SQL = """
SELECT q.candidate_id, c.name, c.city, c.candidate_nit, c.resolution_attempts
FROM ingest.rues_lookup_queue AS q
JOIN ingest.entity_candidate AS c ON c.id = q.candidate_id
WHERE q.status = 'queued' AND (q.next_attempt_at IS NULL OR q.next_attempt_at <= :now)
ORDER BY q.next_attempt_at NULLS FIRST, q.id
LIMIT :batch_size
"""


@dataclass(frozen=True)
class QueueResult:
    """Counters of one ``run_once`` pass."""

    resolved: int = 0
    delivered: int = 0
    retried: int = 0
    quarantined: int = 0
    delivery_failed: int = 0


class RuesQueueWorker:
    """Resolve due queue rows and deliver the resolved candidates to core."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        rues: RuesLookup,
        flow: SnapshotFlow,
        batch_size: int = 25,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine = engine
        self.rues = rues
        self.flow = flow
        self.batch_size = batch_size
        self.now = now

    async def run_once(self) -> QueueResult:
        started = self.now()
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(DUE_SQL), {"now": started, "batch_size": self.batch_size}
            )
            rows = list(result.mappings().all())
        counts = {"resolved": 0, "delivered": 0, "retried": 0, "quarantined": 0, "failed": 0}
        for row in rows:
            candidate = Candidate(
                id=str(row["candidate_id"]),
                name=row["name"],
                city=row["city"],
                nit=row["candidate_nit"],
                attempts=int(row["resolution_attempts"]),
            )
            store = SqlResolutionStore()
            resolution = EntityResolver(self.rues, store, now=started).resolve(candidate)
            async with self.engine.begin() as connection:
                await store.flush(connection)
            if resolution.decision in DELIVER_DECISIONS:
                counts["resolved"] += 1
                if await self._deliver(candidate, resolution.rues_nit, started):
                    counts["delivered"] += 1
                else:
                    counts["failed"] += 1
            elif resolution.decision == "quarantine":
                counts["quarantined"] += 1
            elif resolution.decision in {"new_company", "retry_later"}:
                counts["retried"] += 1
        return QueueResult(
            resolved=counts["resolved"],
            delivered=counts["delivered"],
            retried=counts["retried"],
            quarantined=counts["quarantined"],
            delivery_failed=counts["failed"],
        )

    async def _deliver(self, candidate: Candidate, rues_nit: str | None, started: datetime) -> bool:
        try:
            await self.flow.deliver_company(
                candidate_id=candidate.id,
                name=candidate.name,
                city=candidate.city,
                rues_nit=rues_nit,
            )
            return True
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            error = f"core delivery failed: {type(exc).__name__}"[:ERROR_LIMIT]
            LOGGER.warning("rues queue delivery failed for %s: %s", candidate.id, error)
        # Queue the candidate again in a new short transaction so it is not lost.
        store = SqlResolutionStore()
        store.enqueue_rues_lookup(
            candidate.id, candidate.attempts + 1, started + RETRY_BACKOFF, error
        )
        async with self.engine.begin() as connection:
            await store.flush(connection)
        return False


async def run_forever(worker: RuesQueueWorker, *, interval_seconds: float) -> None:
    """Run ``run_once`` until cancelled, sleeping ``interval_seconds`` between passes."""
    while True:
        try:
            await worker.run_once()
        except Exception:
            LOGGER.exception("rues queue pass failed")
        await asyncio.sleep(interval_seconds)
