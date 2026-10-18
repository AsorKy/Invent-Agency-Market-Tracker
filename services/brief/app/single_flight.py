"""Provide a Redis-backed single-flight lock seam for Brief regeneration.

The lock uses ``SET NX PX`` and owner-checked Lua release. It relates to
``main.py`` and the future ``brief.single_flight_lock`` table; Redis failure
fails open because duplicate regeneration is acceptable while the persistent
digest remains protected by the repository's version check.
"""

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class SingleFlightLock:
    """Coordinate one Brief regeneration per company when Redis is available."""

    def __init__(self, redis: Redis, ttl_ms: int = 30_000) -> None:
        self.redis = redis
        self.ttl_ms = ttl_ms

    async def acquire(self, key: str, owner: str) -> bool:
        """Acquire a lock or fail open when Redis is unavailable."""
        try:
            result = await cast(
                Awaitable[object], self.redis.set(key, owner, nx=True, px=self.ttl_ms)
            )
        except RedisError:
            return True
        return bool(result)

    async def release(self, key: str, owner: str) -> None:
        """Release a lock only when the caller still owns it."""
        try:
            await cast(Awaitable[object], self.redis.eval(RELEASE_LOCK_LUA, 1, key, owner))
        except RedisError:
            return
