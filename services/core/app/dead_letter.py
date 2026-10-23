"""Publish core outbox events with a retry schedule and a dead letter queue.

The algorithm lives in ``libs/messaging/relay.py`` (one implementation, one instance per schema
with an outbox, decisions D-17 and D-21); this module keeps the public names core code and tests
import (``relay_due_events``, ``stream_fields``, ``retry_delay`` and the protocols) and runs the
core instance over ``core.outbox_event`` and ``core.outbox_dead_letter``. It relates to
``outbox_relay.py`` (entry point), ``repository.py`` (SQL unit of work) and ``republish.py``.
"""

import asyncio

from libs.messaging.relay import (
    BACKOFF_SECONDS,
    BATCH_SIZE,
    MISSING_TENANT_ERROR,
    MissingTenantError,
    OutboxTransaction,
    OutboxUnitOfWork,
    Publisher,
    relay_due_events,
    retry_delay,
    run_relay_loop,
    stream_fields,
)

__all__ = [
    "BACKOFF_SECONDS",
    "BATCH_SIZE",
    "MISSING_TENANT_ERROR",
    "MissingTenantError",
    "OutboxTransaction",
    "OutboxUnitOfWork",
    "Publisher",
    "main",
    "relay_due_events",
    "retry_delay",
    "stream_fields",
]


async def main() -> None:
    """Run the core relay loop over ``core.outbox_event``."""
    from libs.common.settings import get_settings
    from services.core.app.outbox_relay import STREAM_NAME
    from services.core.app.repository import CoreRepository

    settings = get_settings()
    repository = CoreRepository(settings.database_url)
    await run_relay_loop(
        service_name="relay",
        open_unit_of_work=repository.outbox_unit_of_work,
        stream_name=STREAM_NAME,
        redis_url=settings.redis_url,
        poll_interval_seconds=settings.outbox_poll_interval_seconds,
        close_store=repository.close,
    )


if __name__ == "__main__":
    asyncio.run(main())
