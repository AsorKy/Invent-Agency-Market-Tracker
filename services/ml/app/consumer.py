"""Consume Core events and invalidate scores that are no longer fresh.

The consumer records ``event_id`` in ``ml.processed_event`` and marks a
company score stale for signals, company changes, and outcomes. It relates to
``services/core/app/outbox_relay.py``, ``MlRepository``, and the scoring
endpoint in ``main.py``. It prevents a score from being presented as current
after its supporting evidence changes.
"""

import asyncio
import json
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

STREAM_NAME = "invent.events"
GROUP_NAME = "ml"
CONSUMER_NAME = "ml-worker"


STALE_EVENT_TYPES = frozenset({"signal.created", "company.updated", "lead.outcome_recorded"})


async def process_event(connection: AsyncConnection, message: dict[str, str]) -> None:
    """Apply one stream message once; unknown or chain-only event types are only recorded."""
    event_id = UUID(message["event_id"])
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
    event_type = message["event_type"]
    if inserted.scalar_one_or_none() is None or event_type not in STALE_EVENT_TYPES:
        return
    event_payload = json.loads(message.get("payload", "{}"))
    company_value = event_payload.get("company_id") or (
        message["aggregate_id"] if event_type == "company.updated" else None
    )
    if company_value is not None:
        await connection.execute(
            text(
                """
                UPDATE ml.lead_score
                SET stale = true
                WHERE company_id = :company_id
                """
            ),
            {"company_id": UUID(company_value)},
        )


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
        raise RuntimeError("DATABASE_URL is required for ml consumer")
    redis = create_redis(settings.redis_url)
    lifecycle: ServiceState | None = None
    try:
        await ensure_consumer_group(redis, STREAM_NAME, GROUP_NAME)
        shutdown = BatchShutdown(redis, STREAM_NAME, GROUP_NAME)
        lifecycle = register_worker_shutdown("ml", shutdown)
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
