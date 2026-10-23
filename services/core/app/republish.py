"""Replay outbox events to one consumer service: ``python -m services.core.app.republish``.

The command requires ``--target`` (pulse, brief or ml), ``--since`` and an explicit ``--confirm``.
It takes a lock key for the target, re-publishes every matching ``core.outbox_event`` in id order
with ``replay: true`` and the ``target`` field, and releases the lock. While the lock exists the
target must refuse its own writes (``is_target_locked``). A second run for the same target fails
fast. Consumers stay idempotent through the inbox (REQ-EVT-004). It relates to ``dead_letter.py``
(``stream_fields``) and ``repository.py``. Cost is O(events) read in pages of 500.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from libs.messaging.relay import MissingTenantError, stream_fields

TARGETS = ("pulse", "brief", "ml")
LOCK_TTL_SECONDS = 6 * 3600
PAGE_SIZE = 500

_log = logging.getLogger(__name__)


class ReplayError(Exception):
    pass


class Locker(Protocol):
    async def acquire(self, key: str, ttl_seconds: int) -> bool: ...
    async def release(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...


EventPage = Callable[[datetime, UUID | None, int, int], Awaitable[list[dict[str, Any]]]]
Publish = Callable[[dict[str, str]], Awaitable[None]]


def lock_key(target: str) -> str:
    return f"republish:lock:{target}"


async def is_target_locked(locker: Locker, target: str) -> bool:
    """Consumers of ``target`` call this before writing and defer while it is True."""
    return await locker.exists(lock_key(target))


def parse_since(value: str) -> datetime:
    """Accept ``YYYY-MM-DD`` or an ISO timestamp; naive values are UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReplayError(f"invalid --since value: {value}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def replay(
    *,
    target: str,
    since: datetime,
    company_id: UUID | None,
    confirm: bool,
    fetch_page: EventPage,
    publish: Publish,
    locker: Locker,
) -> int:
    """Replay events and return how many were published."""
    if target not in TARGETS:
        raise ReplayError(f"target must be one of {', '.join(TARGETS)}")
    if not confirm:
        raise ReplayError("refusing to replay without --confirm")
    key = lock_key(target)
    if not await locker.acquire(key, LOCK_TTL_SECONDS):
        raise ReplayError(f"a republish for {target} is already running")
    published = 0
    try:
        after_id = 0
        while True:
            page = await fetch_page(since, company_id, after_id, PAGE_SIZE)
            if not page:
                break
            for row in page:
                try:
                    fields = stream_fields(row, replay=True, target=target)
                except MissingTenantError:
                    _log.error(
                        "ALERT republish skipped event without tenant event_id=%s",
                        row["event_id"],
                    )
                    continue
                await publish(fields)
                published += 1
            after_id = int(page[-1]["id"])
    finally:
        await locker.release(key)
    _log.info("republish finished target=%s events=%d", target, published)
    return published


class RedisLocker:
    """Lock key stored in Redis with an expiry so a crashed run cannot block forever."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def acquire(self, key: str, ttl_seconds: int) -> bool:
        return bool(await self._redis.set(key, "1", nx=True, ex=ttl_seconds))

    async def release(self, key: str) -> None:
        await self._redis.delete(key)

    async def exists(self, key: str) -> bool:
        return bool(await self._redis.exists(key))


@asynccontextmanager
async def open_io() -> AsyncIterator[tuple[EventPage, Publish, Locker]]:
    """Open the database and Redis for a real run (not used by unit tests)."""
    from libs.common.redis_client import create_redis
    from libs.common.settings import get_settings
    from services.core.app.outbox_relay import STREAM_NAME
    from services.core.app.repository import CoreRepository

    settings = get_settings()
    repository = CoreRepository(settings.database_url)
    redis = create_redis(settings.redis_url)

    async def publish(fields: dict[str, str]) -> None:
        await redis.xadd(STREAM_NAME, fields)  # type: ignore[arg-type]

    try:
        yield repository.list_outbox_events_since, publish, RedisLocker(redis)
    finally:
        await redis.aclose()
        await repository.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="republish", description=__doc__)
    parser.add_argument("--target", required=True, choices=TARGETS)
    parser.add_argument("--since", required=True, help="YYYY-MM-DD or ISO timestamp")
    parser.add_argument("--company-id", type=UUID, default=None)
    parser.add_argument("--confirm", action="store_true", help="required to actually replay")
    return parser


async def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        since = parse_since(args.since)
        if not args.confirm:
            raise ReplayError("refusing to replay without --confirm")
        async with open_io() as (fetch_page, publish, locker):
            count = await replay(
                target=args.target,
                since=since,
                company_id=args.company_id,
                confirm=args.confirm,
                fetch_page=fetch_page,
                publish=publish,
                locker=locker,
            )
    except ReplayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"replayed {count} events to {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
