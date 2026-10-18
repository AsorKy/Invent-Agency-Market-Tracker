"""Unit tests for the outbox relay and the idempotent stream consumers.

Covers REQ-QA-002 (offline unit coverage), REQ-EVT-001 (outbox relay publishes
committed events at least once) and REQ-EVT-002 (consumers apply a repeated
``event_id`` once). The database is an in-memory fake and Redis is fakeredis, so
nothing external is used. The module skips when runtime dependencies are absent.
"""

import json
from types import SimpleNamespace
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


def install_relay(monkeypatch: pytest.MonkeyPatch, db: FakeDb, redis: RedisProxy) -> None:
    monkeypatch.setattr(outbox_relay, "get_settings", make_settings)
    monkeypatch.setattr(outbox_relay, "create_engine", lambda *_a, **_k: db)
    monkeypatch.setattr(outbox_relay, "create_redis", lambda *_a, **_k: redis)


async def stream_entries(redis: Any) -> list[dict[str, str]]:
    return [fields for _id, fields in await redis.xrange(outbox_relay.STREAM_NAME)]


async def test_relay_publishes_pending_events_once_and_marks_them(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    db = FakeDb([outbox_row(1), outbox_row(2, "company.updated")])
    install_relay(monkeypatch, db, RedisProxy(fake_redis))

    assert await outbox_relay.relay_once() == 2
    assert db.published == [1, 2]
    assert db.disposed
    entries = await stream_entries(fake_redis)
    assert [e["event_id"] for e in entries] == [str(r["event_id"]) for r in db.outbox_rows]
    assert [e["event_type"] for e in entries] == ["signal.created", "company.updated"]
    assert json.loads(entries[0]["payload"]) == db.outbox_rows[0]["payload"]

    # A second pass finds nothing pending, so nothing is published twice.
    assert await outbox_relay.relay_once() == 0
    assert len(await stream_entries(fake_redis)) == 2


async def test_relay_without_database_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    monkeypatch.setattr(outbox_relay, "get_settings", make_settings)
    monkeypatch.setattr(outbox_relay, "create_engine", lambda *_a, **_k: None)
    monkeypatch.setattr(outbox_relay, "create_redis", lambda *_a, **_k: RedisProxy(fake_redis))

    assert await outbox_relay.relay_once() == 0
    assert await stream_entries(fake_redis) == []


async def test_relay_failure_keeps_unpublished_rows_pending_for_next_pass(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    db = FakeDb([outbox_row(1), outbox_row(2), outbox_row(3)])
    install_relay(monkeypatch, db, RedisProxy(fake_redis, fail_xadd_on_calls={2}))

    with pytest.raises(ConnectionError):
        await outbox_relay.relay_once()
    assert db.published == [1]
    assert db.disposed

    assert await outbox_relay.relay_once() == 2
    assert db.published == [1, 2, 3]
    ids = [e["event_id"] for e in await stream_entries(fake_redis)]
    assert ids == [str(r["event_id"]) for r in db.outbox_rows]


async def test_relay_mark_failure_republishes_duplicate_at_least_once(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    db = FakeDb([outbox_row(1)])
    db.fail_next("UPDATE core.outbox_event")
    install_relay(monkeypatch, db, RedisProxy(fake_redis))

    with pytest.raises(ConnectionError):
        await outbox_relay.relay_once()
    assert db.published == []
    assert await outbox_relay.relay_once() == 1
    assert db.published == [1]
    # At-least-once: the same event_id is in the stream twice; consumers deduplicate.
    ids = [e["event_id"] for e in await stream_entries(fake_redis)]
    assert ids == [str(db.outbox_rows[0]["event_id"])] * 2


async def test_relay_main_retries_transient_failure_until_published(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    db = FakeDb([outbox_row(1), outbox_row(2)])
    install_relay(monkeypatch, db, RedisProxy(fake_redis, fail_xadd_on_calls={2}))

    async def stop_after_first_pass(_seconds: float) -> None:
        raise StopLoop

    monkeypatch.setattr(
        outbox_relay, "asyncio", SimpleNamespace(sleep=stop_after_first_pass, run=None)
    )
    with pytest.raises(StopLoop):
        await outbox_relay.main()

    assert db.published == [1, 2]
    assert len(await stream_entries(fake_redis)) == 2


async def test_relay_main_survives_exhausted_retries(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    db = FakeDb([outbox_row(1)])
    db.fail_next("FROM core.outbox_event", times=3)
    install_relay(monkeypatch, db, RedisProxy(fake_redis))

    async def stop(_seconds: float) -> None:
        raise StopLoop

    monkeypatch.setattr(outbox_relay, "asyncio", SimpleNamespace(sleep=stop, run=None))
    with pytest.raises(StopLoop):  # reached the poll sleep, so the error was swallowed
        await outbox_relay.main()
    assert db.published == []


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
