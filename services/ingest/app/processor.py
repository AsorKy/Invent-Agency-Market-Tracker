"""Consume ingest snapshots and create entity candidates.

The process creates the ``ingest-processors`` group, reads pending or new
snapshots, and calls ``IngestRepository.process_snapshot``; it acknowledges a
message only after the candidate transaction commits. It relates to
``services/ingest/app/main.py``, ``repository.py``, and
``libs/common/redis_streams.py``. It decouples the webhook from normalization
and retries transient failures.
"""

import asyncio
from uuid import UUID

from libs.common.redis_streams import ensure_consumer_group, read_consumer_batch
from libs.common.settings import get_settings
from services.ingest.app.repository import IngestRepository

STREAM_NAME = "invent.ingest"
GROUP_NAME = "ingest-processors"
CONSUMER_NAME = "ingest-worker"


async def main() -> None:
    settings = get_settings()
    repository = IngestRepository(settings.database_url, settings.redis_url)
    redis = repository.redis
    try:
        await ensure_consumer_group(redis, STREAM_NAME, GROUP_NAME)
        while True:
            messages = await read_consumer_batch(
                redis,
                stream=STREAM_NAME,
                group=GROUP_NAME,
                consumer=CONSUMER_NAME,
                count=25,
                block=5000,
            )
            for _stream, entries in messages:
                for message_id, payload in entries:
                    try:
                        await repository.process_snapshot(UUID(payload["raw_snapshot_id"]))
                    except Exception:
                        # Leave the message pending. The next loop retries it;
                        # ACK only after candidate creation is committed.
                        await asyncio.sleep(1)
                        continue
                    await redis.xack(STREAM_NAME, GROUP_NAME, message_id)
    finally:
        await repository.close()


if __name__ == "__main__":
    asyncio.run(main())
