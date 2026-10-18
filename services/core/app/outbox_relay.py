"""Publish committed events from ``core.outbox_event`` to Redis.

``relay_once`` reads pending events, appends them to ``invent.events``, and
sets ``published_at``; the main process repeats every second. The variant with
the retry schedule and dead letter queue is ``dead_letter.py`` (run it with
``python -m services.core.app.dead_letter``). It relates to
``CoreRepository.create_signal``, the analytics and ML consumers, and the
outbox table in the migration. It decouples Core transactions from
projections and supports at-least-once delivery with retries.
"""

import asyncio
import json

from redis.asyncio import Redis
from sqlalchemy import text

from libs.common.db import create_engine
from libs.common.redis_client import create_redis
from libs.common.retry import retry_with_backoff
from libs.common.settings import get_settings

STREAM_NAME = "invent.events"


async def relay_once() -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=1)
    redis: Redis = create_redis(settings.redis_url)
    if engine is None:
        await redis.aclose()
        return 0
    published = 0
    try:
        async with engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT id, event_id, aggregate_type, aggregate_id, event_type,
                               version, payload
                        FROM core.outbox_event
                        WHERE published_at IS NULL
                        ORDER BY id
                        LIMIT 100
                        """
                        )
                    )
                )
                .mappings()
                .all()
            )
        for row in rows:
            await redis.xadd(
                STREAM_NAME,
                {
                    "event_id": str(row["event_id"]),
                    "event_type": row["event_type"],
                    "aggregate_type": row["aggregate_type"],
                    "aggregate_id": str(row["aggregate_id"]),
                    "version": str(row["version"]),
                    "payload": json.dumps(row["payload"]),
                },
            )
            async with engine.begin() as connection:
                await connection.execute(
                    text("UPDATE core.outbox_event SET published_at = now() WHERE id = :id"),
                    {"id": row["id"]},
                )
            published += 1
    finally:
        await redis.aclose()
        await engine.dispose()
    return published


async def main() -> None:
    settings = get_settings()
    while True:
        try:
            await retry_with_backoff(
                relay_once,
                attempts=settings.worker_retry_attempts,
                base_delay_seconds=settings.worker_retry_base_delay_seconds,
                max_delay_seconds=settings.worker_retry_max_delay_seconds,
                jitter_ratio=settings.worker_retry_jitter_ratio,
                retry_on=(Exception,),
            )
        except Exception:
            # The next pass re-reads unpublished rows; consumers deduplicate
            # duplicate stream entries by event_id.
            pass
        await asyncio.sleep(settings.outbox_poll_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
