"""Consume Core events and invalidate scores that are no longer fresh.

The consumer records ``event_id`` in ``ml.processed_event`` and marks a
company score stale for signals, company changes, and outcomes. It relates to
``services/core/app/outbox_relay.py``, ``MlRepository``, and the scoring
endpoint in ``main.py``. It prevents a score from being presented as current
after its supporting evidence changes.
"""

import asyncio
import json
from uuid import UUID

from sqlalchemy import text

from libs.common.db import create_engine
from libs.common.redis_client import create_redis
from libs.common.redis_streams import ensure_consumer_group, read_consumer_batch
from libs.common.retry import retry_with_backoff
from libs.common.settings import get_settings

STREAM_NAME = "invent.events"
GROUP_NAME = "ml"
CONSUMER_NAME = "ml-worker"


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=2)
    if engine is None:
        raise RuntimeError("DATABASE_URL is required for ml consumer")
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
                            event_id = UUID(message_payload["event_id"])
                            async with engine.begin() as connection:
                                inserted = await connection.execute(
                                    text(
                                        """
                                        INSERT INTO ml.processed_event (event_id)
                                        VALUES (:event_id)
                                        ON CONFLICT (event_id) DO NOTHING
                                        RETURNING event_id
                                        """
                                    ),
                                    {"event_id": event_id},
                                )
                                event_type = message_payload["event_type"]
                                if inserted.scalar_one_or_none() is not None and event_type in {
                                    "signal.created",
                                    "company.updated",
                                    "lead.outcome_recorded",
                                }:
                                    event_payload = json.loads(message_payload.get("payload", "{}"))
                                    company_value = event_payload.get("company_id") or (
                                        message_payload["aggregate_id"]
                                        if event_type == "company.updated"
                                        else None
                                    )
                                    if company_value is not None:
                                        company_id = UUID(company_value)
                                        await connection.execute(
                                            text(
                                                """
                                                UPDATE ml.lead_score
                                                SET stale = true
                                                WHERE company_id = :company_id
                                                """
                                            ),
                                            {"company_id": company_id},
                                        )

                        await retry_with_backoff(
                            process,
                            attempts=settings.worker_retry_attempts,
                            base_delay_seconds=settings.worker_retry_base_delay_seconds,
                            max_delay_seconds=settings.worker_retry_max_delay_seconds,
                            jitter_ratio=settings.worker_retry_jitter_ratio,
                            retry_on=(Exception,),
                        )
                    except Exception:
                        await asyncio.sleep(settings.worker_failure_sleep_seconds)
                        continue
                    await redis.xack(STREAM_NAME, GROUP_NAME, message_id)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
