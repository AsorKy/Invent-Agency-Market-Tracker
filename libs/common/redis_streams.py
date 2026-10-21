"""Provide shared Redis Streams primitives with safe retries.

The module creates consumer groups idempotently and reads pending messages for
the current consumer before waiting for new messages. It relates to
``services/ingest/app/processor.py``, ``pulse/app/consumer.py``, and
``ml/app/consumer.py``; each acknowledges with ``XACK`` only after its database
transaction commits. It prevents a transient failure from leaving a message
pending indefinitely after its first attempt.
"""

from typing import cast

from redis.asyncio import Redis
from redis.exceptions import ResponseError

StreamEntry = tuple[str, dict[str, str]]
StreamBatch = list[tuple[str, list[StreamEntry]]]


async def ensure_consumer_group(redis: Redis, stream: str, group: str) -> None:
    """Create a consumer group and accept an already existing group."""
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def read_consumer_batch(
    redis: Redis, *, stream: str, group: str, consumer: str, count: int, block: int
) -> StreamBatch:
    """Read pending messages, then wait for new messages."""
    pending = cast(
        StreamBatch,
        await redis.xreadgroup(group, consumer, {stream: "0"}, count=count),
    )
    if any(entries for _stream, entries in pending):
        return pending
    return cast(
        StreamBatch,
        await redis.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block),
    )
