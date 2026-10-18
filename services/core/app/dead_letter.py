"""Publish outbox events with a retry schedule and a dead letter queue.

``relay_due_events`` claims events that are due, publishes each one, and on failure records the
attempt and schedules the next one after 10 s, 30 s, 90 s, 5 min and 15 min. A sixth failure moves
the event to ``core.outbox_dead_letter`` and logs an ``ALERT`` line (REQ-EVT-003). Events of an
aggregate stay in order: after one fails, its later events wait in the same pass. It relates to
``outbox_relay.py`` (legacy loop), ``repository.py`` (SQL unit of work) and ``republish.py`` (shares
``stream_fields``). One transaction per pass; cost is O(batch).
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Protocol

BACKOFF_SECONDS = (10, 30, 90, 300, 900)
BATCH_SIZE = 100

MISSING_TENANT_ERROR = "missing_tenant_id"

_log = logging.getLogger(__name__)


class MissingTenantError(ValueError):
    """An outbox row has no tenant and must not be published."""


class OutboxTransaction(Protocol):
    async def claim_due(self, now: datetime, limit: int) -> list[dict[str, Any]]: ...
    async def mark_published(self, row_id: int, now: datetime) -> None: ...
    async def record_failure(
        self, row_id: int, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None: ...
    async def dead_letter(
        self, row: dict[str, Any], *, attempts: int, error: str, now: datetime
    ) -> None: ...


class OutboxUnitOfWork(Protocol):
    async def __aenter__(self) -> OutboxTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


Publisher = Callable[[dict[str, Any]], Awaitable[None]]


def retry_delay(failed_attempts: int) -> timedelta | None:
    """Return the wait before the next try, or None once the retries are used up."""
    if failed_attempts < 1 or failed_attempts > len(BACKOFF_SECONDS):
        return None
    return timedelta(seconds=BACKOFF_SECONDS[failed_attempts - 1])


def stream_fields(
    row: dict[str, Any], *, replay: bool = False, target: str | None = None
) -> dict[str, str]:
    """Build the Redis stream entry for an outbox row; replays carry ``replay`` and ``target``.

    Every entry carries ``tenant_id`` as its own field (decision D-16); a row without one raises
    ``MissingTenantError`` so it can never reach the stream.
    """
    if not row.get("tenant_id"):
        raise MissingTenantError(MISSING_TENANT_ERROR)
    fields = {
        "event_id": str(row["event_id"]),
        "tenant_id": str(row["tenant_id"]),
        "event_type": str(row["event_type"]),
        "aggregate_type": str(row["aggregate_type"]),
        "aggregate_id": str(row["aggregate_id"]),
        "version": str(row["version"]),
        "payload": json.dumps(row["payload"]),
    }
    if replay:
        fields["replay"] = "true"
    if target:
        fields["target"] = target
    return fields


async def relay_due_events(
    unit_of_work: OutboxUnitOfWork,
    publish: Publisher,
    *,
    now: datetime | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, int]:
    """Run one pass; return counts of ``published``, ``retried`` and ``dead_lettered``."""
    moment = now or datetime.now(UTC)
    log = logger or _log
    counts = {"published": 0, "retried": 0, "dead_lettered": 0}
    failed_aggregates: set[Any] = set()
    async with unit_of_work as tx:
        for row in await tx.claim_due(moment, BATCH_SIZE):
            if row["aggregate_id"] in failed_aggregates:
                continue
            if not row.get("tenant_id"):
                attempts = int(row.get("attempts") or 0) + 1
                await tx.dead_letter(row, attempts=attempts, error=MISSING_TENANT_ERROR, now=moment)
                counts["dead_lettered"] += 1
                log.error(
                    "ALERT outbox event dead-lettered event_id=%s event_type=%s error=%s",
                    row["event_id"],
                    row["event_type"],
                    MISSING_TENANT_ERROR,
                )
                continue
            try:
                await publish(row)
            except Exception as exc:
                failed_aggregates.add(row["aggregate_id"])
                attempts = int(row.get("attempts") or 0) + 1
                error = f"{type(exc).__name__}: {exc}"[:500]
                delay = retry_delay(attempts)
                if delay is None:
                    await tx.dead_letter(row, attempts=attempts, error=error, now=moment)
                    counts["dead_lettered"] += 1
                    log.error(
                        "ALERT outbox event dead-lettered event_id=%s event_type=%s attempts=%d",
                        row["event_id"],
                        row["event_type"],
                        attempts,
                    )
                else:
                    await tx.record_failure(
                        row["id"], attempts=attempts, next_attempt_at=moment + delay, error=error
                    )
                    counts["retried"] += 1
                continue
            await tx.mark_published(row["id"], moment)
            counts["published"] += 1
    return counts


async def main() -> None:
    """Relay loop with the retry schedule and dead letter queue."""
    from libs.common.redis_client import create_redis
    from libs.common.settings import get_settings
    from services.core.app.outbox_relay import STREAM_NAME
    from services.core.app.repository import CoreRepository

    settings = get_settings()
    repository = CoreRepository(settings.database_url)
    redis = create_redis(settings.redis_url)

    async def publish(row: dict[str, Any]) -> None:
        fields = stream_fields(row, replay=bool(row.get("replay")))
        await redis.xadd(STREAM_NAME, fields)  # type: ignore[arg-type]

    try:
        while True:
            try:
                await relay_due_events(repository.outbox_unit_of_work(), publish)
            except Exception:
                _log.exception("outbox relay pass failed")
            await asyncio.sleep(settings.outbox_poll_interval_seconds)
    finally:
        await redis.aclose()
        await repository.close()


if __name__ == "__main__":
    asyncio.run(main())
