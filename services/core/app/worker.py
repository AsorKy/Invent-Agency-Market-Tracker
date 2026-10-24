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
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID

from libs.common.events_dispatch import Handler, dispatch_event
from libs.common.redis_client import create_redis
from libs.common.redis_streams import ensure_consumer_group, read_consumer_batch
from libs.common.settings import get_settings
from services.core.app.hot_alert import HotAlertNotifier, HotCallback
from services.core.app.imports import process_next_job
from services.core.app.qualification import (
    QualificationUnitOfWork,
    handle_score_event,
    parse_score_event,
)
from services.core.app.repository import CoreRepository

STREAM_NAME = "invent.events"
GROUP_NAME = "core"
CONSUMER_NAME = "core-worker"

_log = logging.getLogger(__name__)


ScoreWriter = Callable[..., Awaitable[bool]]


def _tenant_of(fields: Mapping[str, str]) -> UUID | None:
    try:
        return UUID(fields["tenant_id"])
    except (KeyError, ValueError):
        return None


def _uuid_of(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def build_handlers(
    unit_of_work_factory: Callable[[], QualificationUnitOfWork],
    on_hot: HotCallback | None = None,
    store_score: ScoreWriter | None = None,
) -> dict[str, Handler]:
    """Map ``lead.score_updated`` to score storage (idempotent, never lowers) then qualification."""

    async def on_score_updated(fields: Mapping[str, str]) -> None:
        if store_score is not None:
            _, company_id, score, _line = parse_score_event(fields)
            extra: dict[str, UUID] = {}
            scored_event_id = _uuid_of(fields.get("scored_event_id"))
            if scored_event_id is not None:
                extra["scored_event_id"] = scored_event_id
            await store_score(
                company_id=company_id,
                tenant_id=_tenant_of(fields),
                score=score,
                source_version=fields.get("source_version") or None,
                now=datetime.now(UTC),
                **extra,
            )
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
    handlers = build_handlers(
        repository.qualification_unit_of_work, notifier, repository.store_company_score
    )
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
