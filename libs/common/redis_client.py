"""Create asynchronous Redis clients and run their health check.

The client uses decoded string responses required by Redis Streams and scoring
locks. It relates indirectly to ``libs.common.settings`` and directly to the
``ingest``, ``core``, ``pulse``, and ``ml`` workers, which use
``create_redis`` for queues, outbox delivery, and events. Its purpose is to
centralize connection setup and ensure processes close clients on shutdown.
"""

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from libs.common.settings import get_settings


def create_redis(redis_url: str) -> Redis:
    settings = get_settings()
    return cast(
        Redis,
        Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=settings.redis_socket_timeout_seconds,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
        ),
    )


async def check_redis(redis_url: str) -> bool:
    client = create_redis(redis_url)
    try:
        return await cast(Awaitable[bool], client.ping())
    except Exception:
        return False
    finally:
        await client.aclose()
