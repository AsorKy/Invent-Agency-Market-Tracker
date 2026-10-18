"""Consume ingest snapshots and create entity candidates.

The process creates the ``ingest-processors`` group, reads pending or new
snapshots, and calls ``IngestRepository.process_snapshot``; it acknowledges a
message only after the candidate transaction commits. It relates to
``services/ingest/app/main.py``, ``repository.py``, and
``libs/common/redis_streams.py``. It decouples the webhook from normalization
and retries transient failures.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from libs.common.redis_streams import ensure_consumer_group, read_consumer_batch
from libs.common.retry import retry_with_backoff
from libs.common.settings import get_settings
from libs.domain.entity_resolution import EntityResolver, MockRuesLookup
from services.ingest.app.repository import IngestRepository
from services.ingest.app.to_core import (
    HttpCoreClient,
    SnapshotFlow,
    SqlFailureSink,
    SqlResolutionStore,
)

STREAM_NAME = "invent.ingest"
GROUP_NAME = "ingest-processors"
CONSUMER_NAME = "ingest-worker"


async def main() -> None:
    settings = get_settings()
    repository = IngestRepository(
        settings.database_url,
        settings.redis_url,
        enqueue_mark_ttl_seconds=settings.ingest_enqueue_mark_ttl_seconds,
    )
    redis = repository.redis
    core = HttpCoreClient(
        settings.core_url,
        settings.internal_service_token,
        timeout_seconds=settings.upstream_timeout_seconds,
    )
    if repository.engine is not None:
        store = SqlResolutionStore()
        # No live RUES client exists yet (REQ-ING-003 uses a mock lookup).
        rues = MockRuesLookup([])
        repository.resolution_store = store
        repository.flow = SnapshotFlow(
            core=core,
            failures=SqlFailureSink(repository.engine),
            resolve=lambda candidate: EntityResolver(rues, store, now=datetime.now(UTC)).resolve(
                candidate
            ),
            dataset_sources={
                str(dataset_id): source
                for source in settings.enabled_sources()
                if (dataset_id := getattr(settings, f"source_{source}_dataset_id", None))
            },
            retry_attempts=settings.upstream_retry_attempts,
            retry_base_delay_seconds=settings.upstream_retry_base_delay_seconds,
            retry_max_delay_seconds=settings.upstream_retry_max_delay_seconds,
        )
    try:
        await ensure_consumer_group(redis, STREAM_NAME, GROUP_NAME)
        while True:
            messages = await read_consumer_batch(
                redis,
                stream=STREAM_NAME,
                group=GROUP_NAME,
                consumer=CONSUMER_NAME,
                count=25,
                block=settings.worker_stream_block_ms,
            )
            for _stream, entries in messages:
                for message_id, payload in entries:
                    try:
                        snapshot_id = UUID(payload["raw_snapshot_id"])

                        async def process(snapshot_id: UUID = snapshot_id) -> int:
                            return await repository.process_snapshot(snapshot_id)

                        await retry_with_backoff(
                            process,
                            attempts=settings.worker_retry_attempts,
                            base_delay_seconds=settings.worker_retry_base_delay_seconds,
                            max_delay_seconds=settings.worker_retry_max_delay_seconds,
                            jitter_ratio=settings.worker_retry_jitter_ratio,
                            retry_on=(Exception,),
                        )
                    except Exception:
                        # Leave the message pending. The next loop retries it;
                        # ACK only after candidate creation is committed.
                        await asyncio.sleep(settings.worker_failure_sleep_seconds)
                        continue
                    await redis.xack(STREAM_NAME, GROUP_NAME, message_id)
    finally:
        await core.aclose()
        await repository.close()


if __name__ == "__main__":
    asyncio.run(main())
