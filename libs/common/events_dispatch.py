"""Dispatch stream events to handlers and tolerate unknown event types.

A consumer reads an entry from ``invent.events`` and calls ``dispatch_event``.
Known types run their handler; reserved L2/L3 names and any unknown type are
acknowledged and logged as one line without the payload (REQ-EXT-001,
REQ-EVT-005). The module uses only the standard library.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping

Handler = Callable[[Mapping[str, str]], Awaitable[None]]
Ack = Callable[[], Awaitable[None]]

_log = logging.getLogger(__name__)


async def dispatch_event(
    fields: Mapping[str, str],
    handlers: Mapping[str, Handler],
    ack: Ack,
    logger: logging.Logger | None = None,
) -> bool:
    """Run the handler for the entry type; return True when a handler ran.

    An entry without a handler is acknowledged and one line with the type and
    event id is logged. Handler errors propagate and the entry is not acked.
    """
    event_type = str(fields.get("event_type", ""))
    handler = handlers.get(event_type)
    if handler is None:
        (logger or _log).info(
            "event ignored: no handler event_type=%s event_id=%s",
            event_type[:64],
            str(fields.get("event_id", ""))[:64],
        )
        await ack()
        return False
    await handler(fields)
    await ack()
    return True
