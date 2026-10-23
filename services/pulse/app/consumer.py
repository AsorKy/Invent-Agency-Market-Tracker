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
import signal
from typing import Any
from uuid import UUID

import structlog
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from libs.common.db import create_engine
from libs.common.lifecycle import (
    ServiceState,
    drain_and_run_hooks,
    instrument_app,
    register_shutdown_hook,
)
from libs.common.redis_client import create_redis
from libs.common.redis_streams import ensure_consumer_group, read_consumer_batch
from libs.common.retry import retry_with_backoff
from libs.common.settings import get_settings
from libs.common.tenant import set_tenant_context
from services.pulse.app.project_projection import apply_project_created, apply_stage_change
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
    elif event_type == "signal.converted_to_project":
        await apply_project_created(connection, tenant_id, payload)
    elif event_type == "project.pipeline_updated":
        await apply_stage_change(connection, tenant_id, payload, "to")
    elif event_type == "project.forecast_updated":
        await apply_stage_change(connection, tenant_id, payload, "stage")


async def _dead_letter(redis: Any, message_id: str, fields: dict[str, str], error: str) -> None:
    """Move a poison entry to the dead letter stream, then ack it; never raises."""
    _log.error("ALERT pulse poison entry id=%s error=%s", message_id, error)
    try:
        entry = {**fields, "source_message_id": str(message_id), "error": error}
        await redis.xadd(DEAD_LETTER_STREAM, entry)
        await redis.xack(STREAM_NAME, GROUP_NAME, message_id)
    except Exception:
        _log.exception("pulse could not dead-letter entry id=%s", message_id)


class BatchShutdown:
    """Shutdown state of a stream worker: a stop flag and ids processed but not yet acknowledged.

    The hook registered with ``register_shutdown_hook`` (REQ-EVT-007) acknowledges those ids so a
    committed message is not redelivered after a restart.
    """

    def __init__(self, redis: Any, stream: str, group: str) -> None:
        self.redis = redis
        self.stream = stream
        self.group = group
        self.stop = asyncio.Event()
        self.unacked: list[str] = []

    async def ack(self, message_id: str) -> None:
        """Acknowledge one committed message; keep the id queued if Redis fails."""
        self.unacked.append(message_id)
        await self.redis.xack(self.stream, self.group, message_id)
        self.unacked.remove(message_id)

    async def hook(self) -> None:
        """Stop reading and acknowledge every committed message still queued."""
        self.stop.set()
        pending, self.unacked = self.unacked, []
        if pending:
            await self.redis.xack(self.stream, self.group, *pending)


def bind_request_id(fields: dict[str, str]) -> None:
    """Bind the ``request_id`` carried by a stream entry to the worker logs, or clear it."""
    structlog.contextvars.clear_contextvars()
    request_id = fields.get("request_id")
    if not request_id:
        try:
            body = json.loads(fields.get("payload", "{}"))
            request_id = body.get("request_id") if isinstance(body, dict) else None
        except ValueError:
            request_id = None
    if request_id:
        structlog.contextvars.bind_contextvars(request_id=str(request_id))


def register_worker_shutdown(name: str, shutdown: BatchShutdown) -> ServiceState:
    """Create the worker lifecycle state, register the hook, and stop on SIGTERM."""
    worker = FastAPI()
    state = instrument_app(worker, name)
    register_shutdown_hook(worker, shutdown.hook)
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, shutdown.stop.set)
    return state


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=2)
    if engine is None:
        raise RuntimeError("DATABASE_URL is required for pulse consumer")
    redis = create_redis(settings.redis_url)
    lifecycle: ServiceState | None = None
    try:
        await ensure_consumer_group(redis, STREAM_NAME, GROUP_NAME)
        shutdown = BatchShutdown(redis, STREAM_NAME, GROUP_NAME)
        lifecycle = register_worker_shutdown("pulse", shutdown)
        while not shutdown.stop.is_set():
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
                    if shutdown.stop.is_set():
                        break
                    message_payload = dict(payload)
                    bind_request_id(message_payload)
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
                    await shutdown.ack(message_id)
    finally:
        if lifecycle is not None:
            await drain_and_run_hooks(lifecycle)
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
