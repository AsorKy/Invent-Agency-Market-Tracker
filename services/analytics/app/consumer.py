"""Consume Core events and project signals into analytics.

The consumer creates the Redis ``analytics`` group, records each ``event_id``
in ``analytics.processed_event``, and materializes ``signal.created`` in
``analytics.fact_signal`` within a transaction. It relates to
``services/core/app/outbox_relay.py``, ``AnalyticsRepository``, and the SQL
migration. It is used as an idempotent worker so market endpoints read
rebuildable projections.
"""

import asyncio
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from libs.common.db import create_engine
from libs.common.redis_client import create_redis
from libs.common.redis_streams import ensure_consumer_group, read_consumer_batch
from libs.common.retry import retry_with_backoff
from libs.common.settings import get_settings

STREAM_NAME = "invent.events"
GROUP_NAME = "analytics"
CONSUMER_NAME = "analytics-worker"


async def process_event(connection: AsyncConnection, event: dict[str, str]) -> None:
    event_id = UUID(event["event_id"])
    inserted = await connection.execute(
        text(
            """
            INSERT INTO analytics.processed_event (event_id)
            VALUES (:event_id)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """
        ),
        {"event_id": event_id},
    )
    if inserted.scalar_one_or_none() is None:
        return

    if event["event_type"] != "signal.created":
        return
    payload = json.loads(event["payload"])
    await connection.execute(
        text(
            """
            INSERT INTO analytics.fact_signal
                (signal_id, company_id, source, signal_type, observed_at, strength)
            VALUES (:signal_id, :company_id, :source, :signal_type,
                    :observed_at, :strength)
            ON CONFLICT (signal_id) DO NOTHING
            """
        ),
        {
            "signal_id": UUID(payload["signal_id"]),
            "company_id": UUID(payload["company_id"]),
            "source": payload["source"],
            "signal_type": payload["signal_type"],
            "observed_at": datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00")),
            "strength": payload["strength"],
        },
    )


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=2)
    if engine is None:
        raise RuntimeError("DATABASE_URL is required for analytics consumer")
    redis = create_redis(settings.redis_url)
    try:
        await ensure_consumer_group(redis, STREAM_NAME, GROUP_NAME)
        while True:
            messages = await read_consumer_batch(
                redis,
                stream=STREAM_NAME,
                group=GROUP_NAME,
                consumer=CONSUMER_NAME,
                count=50,
                block=settings.worker_stream_block_ms,
            )
            for _stream, entries in messages:
                for message_id, payload in entries:
                    try:
                        message_payload = dict(payload)

                        async def process(
                            message_payload: dict[str, str] = message_payload,
                        ) -> None:
                            async with engine.begin() as connection:
                                await process_event(connection, message_payload)

                        await retry_with_backoff(
                            process,
                            attempts=settings.worker_retry_attempts,
                            base_delay_seconds=settings.worker_retry_base_delay_seconds,
                            max_delay_seconds=settings.worker_retry_max_delay_seconds,
                            jitter_ratio=settings.worker_retry_jitter_ratio,
                            retry_on=(Exception,),
                        )
                    except Exception:
                        # Leave failed messages pending for the next loop; ACK
                        # only after the warehouse write commits.
                        await asyncio.sleep(settings.worker_failure_sleep_seconds)
                        continue
                    await redis.xack(STREAM_NAME, GROUP_NAME, message_id)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
