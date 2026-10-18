"""Consume Core events and project signals into the Pulse schema.

The consumer creates the Redis ``pulse`` group, records each ``event_id`` in the
consumer-owned ``pulse.processed_event``, and materializes ``signal.created`` in
``pulse.fact_signal`` within one transaction, and updates only the affected
local day of ``pulse.daily_rollup`` (``rollups.py``) for ``signal.created`` and
``lead.outcome_recorded``. It relates to
``services/core/app/outbox_relay.py``, ``PulseRepository``, and the Pulse SQL
migration. It is an idempotent worker so Pulse endpoints read rebuildable
projections. Events without a tenant id cannot be stored under the tenant
policies, so they are skipped and acknowledged.
"""

import asyncio
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from libs.common.db import create_engine
from libs.common.redis_client import create_redis
from libs.common.redis_streams import ensure_consumer_group, read_consumer_batch
from libs.common.retry import retry_with_backoff
from libs.common.settings import get_settings
from libs.common.tenant import set_tenant_context
from services.pulse.app.rollups import apply_outcome, apply_signal

STREAM_NAME = "invent.events"
GROUP_NAME = "pulse"
CONSUMER_NAME = "pulse-worker"


def _tenant_id(event: dict[str, str], payload: dict[str, object]) -> UUID | None:
    """Return the tenant id from the event envelope or the payload, if present."""
    raw = event.get("tenant_id") or payload.get("tenant_id")
    return UUID(str(raw)) if raw else None


async def process_event(connection: AsyncConnection, event: dict[str, str]) -> None:
    """Apply one stream message once; a repeated ``event_id`` is a no-op."""
    event_id = UUID(event["event_id"])
    payload = json.loads(event["payload"])
    tenant_id = _tenant_id(event, payload)
    if tenant_id is None:
        return
    await set_tenant_context(connection, tenant_id)
    inserted = await connection.execute(
        text(
            """
            INSERT INTO pulse.processed_event (event_id, tenant_id, event_type, replay)
            VALUES (:event_id, :tenant_id, :event_type, :replay)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """
        ),
        {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "event_type": event["event_type"],
            "replay": event.get("replay", "false") == "true",
        },
    )
    if inserted.scalar_one_or_none() is None:
        return

    if event["event_type"] == "signal.created":
        await apply_signal(connection, tenant_id, payload)
    elif event["event_type"] == "lead.outcome_recorded":
        await apply_outcome(connection, tenant_id, payload)


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=2)
    if engine is None:
        raise RuntimeError("DATABASE_URL is required for pulse consumer")
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
