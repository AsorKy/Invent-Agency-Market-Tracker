"""Entry point of the outbox relay; the dead letter loop is the only relay (decision D-17).

It keeps the ``invent.events`` stream name and the module path that compose and ``procs.toml``
run (``python -m services.core.app.outbox_relay``). ``main`` delegates to ``dead_letter.main``,
which publishes with the retry schedule and the dead letter queue. It relates to ``dead_letter.py``,
``republish.py`` and the pulse and ML consumers. REQ-EVT-001, REQ-EVT-003, REQ-EVT-006.
"""

import asyncio

STREAM_NAME = "invent.events"


async def main() -> None:
    """Run the dead letter relay loop."""
    from services.core.app import dead_letter

    await dead_letter.main()


if __name__ == "__main__":
    asyncio.run(main())
