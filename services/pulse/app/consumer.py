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
import logging
from typing import Any
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
DEAD_LETTER_STREAM = "invent.events.dead"

_log = logging.getLogger(__name__)


class MalformedEventError(ValueError):
    """A stream entry cannot be parsed; retrying it never helps (poison)."""


def parse_event(event: dict[str, str]) -> tuple[UUID, str, dict[str, object], UUID | None]:
    """Return ``(event_id, event_type, payload, tenant_id)`` or raise ``MalformedEventError``."""
    try:
        event_id = UUID(event["event_id"])
        event_type = event["event_type"]
        payload = json.loads(event["payload"])
        if not isinstance(payload, dict):
            raise TypeError("payload is not an object")
        return event_id, event_type, payload, _tenant_id(event, payload)
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise MalformedEventError(f"{type(exc).__name__}: {exc}"[:300]) from exc


def _tenant_id(event: dict[str, str], payload: dict[str, object]) -> UUID | None:
    """Return the tenant id from the event envelope or the payload, if present."""
    raw = event.get("tenant_id") or payload.get("tenant_id")
    return UUID(str(raw)) if raw else None


async def process_event(connection: AsyncConnection, event: dict[str, str]) -> None:
    """Apply one stream message once; a repeated ``event_id`` is a no-op."""
    event_id, event_type, payload, tenant_id = parse_event(event)
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
            "event_type": event_type,
            "replay": event.get("replay", "false") == "true",
        },
    )
    if inserted.scalar_one_or_none() is None:
        return

    if event_type == "signal.created":
        await apply_signal(connection, tenant_id, payload)
    elif event_type == "lead.outcome_recorded":
        await apply_outcome(connection, tenant_id, payload)


async def _dead_letter(redis: Any, message_id: str, fields: dict[str, str], error: str) -> None:
    """Move a poison entry to the dead letter stream, then ack it; never raises."""
    _log.error("ALERT pulse poison entry id=%s error=%s", message_id, error)
    try:
        entry = {**fields, "source_message_id": str(message_id), "error": error}
        await redis.xadd(DEAD_LETTER_STREAM, entry)
        await redis.xack(STREAM_NAME, GROUP_NAME, message_id)
    except Exception:
        _log.exception("pulse could not dead-letter entry id=%s", message_id)


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
                    message_payload = dict(payload)
                    try:
                        parse_event(message_payload)
                    except MalformedEventError as exc:
                        await _dead_letter(redis, message_id, message_payload, str(exc))
                        continue
                    try:

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
