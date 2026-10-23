"""Entry point of the ml outbox relay: the shared relay over ``ml.outbox_event`` (D-17, D-21).

Same algorithm as the core relay (``libs/messaging/relay.py``), run as its own process with the
ml unit of work, so it reads and writes only ``ml.*`` (INV-01). Publishes ``signal.scored`` to
the ``invent.events`` stream with its tenant. Relates to ``repository.py`` (SQL),
``scoring_service.py`` (writes the rows) and ``services/core/app/outbox_relay.py``.
REQ-EVT-001, REQ-EVT-003, REQ-LNS-012.
"""

import asyncio

STREAM_NAME = "invent.events"


async def main() -> None:
    """Run the ml relay loop."""
    from libs.common.settings import get_settings
    from libs.messaging.relay import run_relay_loop
    from services.ml.app.repository import MlRepository

    settings = get_settings()
    repository = MlRepository(settings.database_url)
    await run_relay_loop(
        service_name="ml-relay",
        open_unit_of_work=repository.outbox_unit_of_work,
        stream_name=STREAM_NAME,
        redis_url=settings.redis_url,
        poll_interval_seconds=settings.outbox_poll_interval_seconds,
        close_store=repository.close,
    )


if __name__ == "__main__":
    asyncio.run(main())
