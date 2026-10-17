"""Create asynchronous Redis clients and run their health check.

The client uses decoded string responses required by Redis Streams and scoring
locks. It relates indirectly to ``libs.common.settings`` and directly to the
``ingest``, ``core``, ``analytics``, and ``ml`` workers, which use
``create_redis`` for queues, outbox delivery, and events. Its purpose is to
centralize connection setup and ensure processes close clients on shutdown.
"""

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis


def create_redis(redis_url: str) -> Redis:
    return Redis.from_url(redis_url, decode_responses=True)


async def check_redis(redis_url: str) -> bool:
    client = create_redis(redis_url)
    try:
        return await cast(Awaitable[bool], client.ping())
    except Exception:
        return False
    finally:
        await client.aclose()
