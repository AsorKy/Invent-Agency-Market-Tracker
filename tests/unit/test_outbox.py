"""Unit tests for the outbox relay and the idempotent stream consumers.

Covers REQ-QA-002 (offline unit coverage), REQ-EVT-001 (outbox relay publishes
committed events at least once) and REQ-EVT-002 (consumers apply a repeated
``event_id`` once). The database is an in-memory fake and Redis is fakeredis, so
nothing external is used. The module skips when runtime dependencies are absent.
"""

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("sqlalchemy")
pytest.importorskip("structlog")
pytest.importorskip("redis.asyncio")
pytest.importorskip("fakeredis")

from support_outbox import (  # noqa: E402
    FakeDb,
    RedisProxy,
    StopLoop,
    make_settings,
)

from services.core.app import outbox_relay  # noqa: E402
from services.ml.app import consumer as ml_consumer  # noqa: E402

COMPANY_ID = uuid4()


def outbox_row(row_id: int, event_type: str = "signal.created") -> dict[str, Any]:
    payload = {
        "signal_id": str(uuid4()),
        "company_id": str(COMPANY_ID),
        "source": "rues",
        "signal_type": "new_company",
        "observed_at": "2026-01-02T03:04:05Z",
        "strength": 0.5,
    }
    return {
        "id": row_id,
        "event_id": uuid4(),
        "aggregate_type": "signal",
        "aggregate_id": uuid4(),
        "event_type": event_type,
        "version": 1,
        "payload": payload,
    }


def stream_message(
    event_type: str = "signal.created", event_id: UUID | None = None
) -> dict[str, str]:
    row = outbox_row(1, event_type)
    return {
        "event_id": str(event_id or row["event_id"]),
        "event_type": event_type,
        "aggregate_type": row["aggregate_type"],
        "aggregate_id": str(COMPANY_ID),
        "version": "1",
        "payload": json.dumps(row["payload"]),
    }


def test_legacy_relay_loop_is_gone_and_main_is_the_dead_letter_loop() -> None:
    assert not hasattr(outbox_relay, "relay_once")
    assert outbox_relay.STREAM_NAME == "invent.events"
    assert callable(outbox_relay.main)


def install_consumer(
    monkeypatch: pytest.MonkeyPatch, module: Any, db: FakeDb, redis: Any, max_reads: int
) -> None:
    """Patch a consumer module and stop its loop after ``max_reads`` batch reads."""
    real_read = module.read_consumer_batch
    reads = {"count": 0}

    async def limited_read(*args: Any, **kwargs: Any) -> Any:
        if reads["count"] >= max_reads:
            raise StopLoop
        reads["count"] += 1
        return await real_read(*args, **kwargs)

    monkeypatch.setattr(module, "get_settings", make_settings)
    monkeypatch.setattr(module, "create_engine", lambda *_a, **_k: db)
    monkeypatch.setattr(module, "create_redis", lambda *_a, **_k: RedisProxy(redis))
    monkeypatch.setattr(module, "read_consumer_batch", limited_read)


async def pending_count(redis: Any, group: str) -> int:
    info = await redis.xpending(outbox_relay.STREAM_NAME, group)
    return int(info["pending"])


@pytest.mark.parametrize("event_type", ["signal.created", "company.updated"])
async def test_ml_consumer_marks_score_stale_once_for_repeated_event(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any, event_type: str
) -> None:
    db = FakeDb()
    message = stream_message(event_type)
    for _ in range(3):
        await fake_redis.xadd(outbox_relay.STREAM_NAME, message)
    install_consumer(monkeypatch, ml_consumer, db, fake_redis, max_reads=2)

    with pytest.raises(StopLoop):
        await ml_consumer.main()

    assert db.processed["ml"] == {UUID(message["event_id"])}
    assert db.stale_updates == [COMPANY_ID]
    assert await pending_count(fake_redis, ml_consumer.GROUP_NAME) == 0


async def test_ml_consumer_acks_but_does_not_stale_unrelated_event(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    db = FakeDb()
    message = stream_message("lead.created")
    await fake_redis.xadd(outbox_relay.STREAM_NAME, message)
    install_consumer(monkeypatch, ml_consumer, db, fake_redis, max_reads=1)

    with pytest.raises(StopLoop):
        await ml_consumer.main()

    assert db.processed["ml"] == {UUID(message["event_id"])}
    assert db.stale_updates == []
    assert await pending_count(fake_redis, ml_consumer.GROUP_NAME) == 0
