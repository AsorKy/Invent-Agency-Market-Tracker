"""Implement a Redis-backed sliding-window request limiter.

The limiter stores request timestamps in a Redis sorted set and uses one Lua
script to remove expired entries, count the active window, and add an allowed
request atomically. It relates to the gateway, Redis configuration, and the
public API contract. Redis supplies the timestamp inside the Lua transaction,
so gateway replicas do not depend on synchronized host clocks. The limiter
uses O(log n + r) work per request, where ``r`` is the number of expired
entries removed, and O(n) memory per key for the active window.
"""

import hashlib
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from redis.asyncio import Redis

_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local window_seconds = tonumber(ARGV[1])
local member = ARGV[2]
local limit = tonumber(ARGV[3])
local clock = redis.call('TIME')
local now_ms = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local window_ms = window_seconds * 1000
local window_start_ms = now_ms - window_ms
redis.call('ZREMRANGEBYSCORE', key, 0, window_start_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after_ms = tonumber(oldest[2]) + window_ms - now_ms
    return {0, count, oldest[2], math.max(1, math.ceil(retry_after_ms / 1000))}
end
redis.call('ZADD', key, now_ms, member)
redis.call('EXPIRE', key, window_seconds)
return {1, count + 1, 0, 0}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int | None = None


class RedisSlidingWindowRateLimiter:
    """Apply a shared request limit to one logical identity."""

    def __init__(self, redis: Redis, *, limit: int, window_seconds: int) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate limit values must be greater than zero")
        self.redis = redis
        self.limit = limit
        self.window_seconds = window_seconds

    async def check(self, identity: str) -> RateLimitDecision:
        """Record a request and return whether it is allowed."""
        identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        key = f"invent:rate:{identity_hash}"
        raw_result = self.redis.eval(
            _SLIDING_WINDOW_SCRIPT,
            1,
            key,
            self.window_seconds,
            str(uuid4()),
            self.limit,
        )
        result = cast(list[int | str], await cast(Awaitable[Any], raw_result))
        allowed = bool(int(result[0]))
        count = int(result[1])
        retry_after = None
        if not allowed:
            retry_after = max(1, int(result[3]))
        return RateLimitDecision(
            allowed=allowed,
            limit=self.limit,
            remaining=max(0, self.limit - count),
            retry_after_seconds=retry_after,
        )
