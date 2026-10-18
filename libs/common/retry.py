"""Provide bounded asynchronous retries with exponential backoff and jitter.

``retry_with_backoff`` executes an async operation again after retryable
failures, increasing the delay up to a configured cap and adding random jitter.
It relates to gateway upstream calls, Redis workers, and the service settings;
callers must use it only for idempotent operations or operations with an
idempotency key. Its purpose is to absorb short network interruptions without
creating synchronized retry storms.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable


async def retry_with_backoff[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 0.1,
    max_delay_seconds: float = 2.0,
    jitter_ratio: float = 0.2,
    retry_on: tuple[type[Exception], ...] = (Exception,),
) -> T:
    """Run an async operation with bounded exponential retries."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if base_delay_seconds < 0 or max_delay_seconds < 0:
        raise ValueError("retry delays must not be negative")
    if not 0 <= jitter_ratio <= 1:
        raise ValueError("jitter_ratio must be between 0 and 1")

    for attempt in range(attempts):
        try:
            return await operation()
        except retry_on:
            if attempt == attempts - 1:
                raise
            delay = min(max_delay_seconds, base_delay_seconds * (2**attempt))
            delay = min(max_delay_seconds, delay + random.uniform(0, delay * jitter_ratio))
            await asyncio.sleep(delay)

    raise RuntimeError("retry operation exited unexpectedly")
