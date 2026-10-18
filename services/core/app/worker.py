"""Run the Core worker: qualify leads from score events and run CSV import jobs.

The stream loop reads ``invent.events`` through the ``core`` consumer group and dispatches events
with ``dispatch_event`` (unknown types are acknowledged and logged). ``handle_score_event``
commits the inbox row and the lead changes before the entry is acknowledged, so redelivery is
safe. ``import_loop`` runs queued import jobs beside it. It relates to
``services/core/app/qualification.py``, ``imports.py``, ``repository.py`` and
``libs/common/redis_streams.py``. A failed entry stays pending and is retried after a pause.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping

from libs.common.events_dispatch import Handler, dispatch_event
from libs.common.redis_client import create_redis
from libs.common.redis_streams import ensure_consumer_group, read_consumer_batch
from libs.common.settings import get_settings
from services.core.app.hot_alert import HotAlertNotifier, HotCallback
from services.core.app.imports import process_next_job
from services.core.app.qualification import QualificationUnitOfWork, handle_score_event
from services.core.app.repository import CoreRepository

STREAM_NAME = "invent.events"
GROUP_NAME = "core"
CONSUMER_NAME = "core-worker"

_log = logging.getLogger(__name__)


def build_handlers(
    unit_of_work_factory: Callable[[], QualificationUnitOfWork],
    on_hot: HotCallback | None = None,
) -> dict[str, Handler]:
    async def on_score_updated(fields: Mapping[str, str]) -> None:
        await handle_score_event(unit_of_work_factory(), fields, on_hot=on_hot)

    return {"lead.score_updated": on_score_updated}


async def import_loop(repository: CoreRepository, idle_seconds: float) -> None:
    """Run queued CSV import jobs one at a time; sleep when none is waiting."""
    while True:
        try:
            status = await process_next_job(repository.import_unit_of_work)
        except Exception:
            _log.exception("import job crashed")
            status = None
        if status is None:
            await asyncio.sleep(idle_seconds)


async def main() -> None:
    settings = get_settings()
    repository = CoreRepository(settings.database_url)
    if repository.engine is None:
        raise RuntimeError("DATABASE_URL is required for the core worker")
    redis = create_redis(settings.redis_url)
    notifier = HotAlertNotifier(settings.hot_lead_webhook_url, clock=time.monotonic)
    handlers = build_handlers(repository.qualification_unit_of_work, notifier)
    imports = asyncio.create_task(import_loop(repository, settings.worker_failure_sleep_seconds))
    try:
        await ensure_consumer_group(redis, STREAM_NAME, GROUP_NAME)
        while True:
            batch = await read_consumer_batch(
                redis,
                stream=STREAM_NAME,
                group=GROUP_NAME,
                consumer=CONSUMER_NAME,
                count=50,
                block=settings.worker_stream_block_ms,
            )
            for _stream, entries in batch:
                for message_id, fields in entries:

                    async def ack(message_id: str = message_id) -> None:
                        await redis.xack(STREAM_NAME, GROUP_NAME, message_id)

                    try:
                        await dispatch_event(fields, handlers, ack)
                    except ValueError:
                        _log.warning("invalid event dropped message_id=%s", message_id)
                        await ack()
                    except Exception:
                        _log.exception("core event failed message_id=%s", message_id)
                        await asyncio.sleep(settings.worker_failure_sleep_seconds)
    finally:
        imports.cancel()
        await notifier.drain()
        await redis.aclose()
        await repository.close()


if __name__ == "__main__":
    asyncio.run(main())
