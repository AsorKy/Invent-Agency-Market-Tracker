"""Unit tests for the Pulse event consumer merged from the analytics service.

Covers REQ-ARC-001 (analytics merged into pulse) and REQ-EVT-002 (a repeated
``event_id`` is applied once). The database is an in-memory fake that dispatches
on SQL text and Redis is fakeredis, so no external service is used. The module
skips when runtime dependencies are absent.
"""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("sqlalchemy")
pytest.importorskip("structlog")
pytest.importorskip("redis.asyncio")
pytest.importorskip("fakeredis")

from support_outbox import RedisProxy, StopLoop, make_settings  # noqa: E402

from services.pulse.app import consumer as pulse_consumer  # noqa: E402

TENANT_ID = uuid4()
STREAM = pulse_consumer.STREAM_NAME


class _Result:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _Transaction:
    def __init__(self, db: "FakePulseDb") -> None:
        self._db = db
        self._staged: list[Callable[[], None]] = []

    async def __aenter__(self) -> "_Transaction":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            for apply in self._staged:
                apply()
        self._staged.clear()

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        return self._db.dispatch(sql, params or {}, self._staged)


class FakePulseDb:
    """Dispatch the statements the Pulse consumer issues and keep committed state."""

    def __init__(self) -> None:
        self.processed: dict[UUID, dict[str, Any]] = {}
        self.fact_signals: dict[UUID, dict[str, Any]] = {}
        self.tenant_contexts: list[str] = []
        self.stage_updates: list[dict[str, Any]] = []
        self.fail_marker: str | None = None
        self.fail_times = 0

    def fail_next(self, marker: str, times: int) -> None:
        self.fail_marker, self.fail_times = marker, times

    def begin(self) -> _Transaction:
        return _Transaction(self)

    async def dispose(self) -> None:
        return None

    def dispatch(
        self, sql: str, params: dict[str, Any], staged: list[Callable[[], None]]
    ) -> _Result:
        if self.fail_marker and self.fail_times > 0 and self.fail_marker in sql:
            self.fail_times -= 1
            raise ConnectionError("simulated database failure")
        if "set_config('app.tenant_id'" in sql:
            self.tenant_contexts.append(params["tenant_id"])
            return _Result()
        if "INSERT INTO pulse.processed_event" in sql:
            event_id = params["event_id"]
            if event_id in self.processed:
                return _Result(scalar=None)
            staged.append(lambda: self.processed.setdefault(event_id, params))
            return _Result(scalar=event_id)
        if "INSERT INTO pulse.fact_signal" in sql:
            staged.append(lambda: self.fact_signals.setdefault(params["signal_id"], params))
            return _Result()
        if "UPDATE pulse.project_snapshot" in sql:
            self.stage_updates.append(params)
            return _Result()
        raise AssertionError(f"unexpected SQL: {sql}")


def stream_message(
    event_type: str = "signal.created", tenant_id: UUID | None = TENANT_ID
) -> dict[str, str]:
    payload = {
        "signal_id": str(uuid4()),
        "company_id": str(uuid4()),
        "source": "rues",
        "signal_type": "new_company",
        "observed_at": "2026-01-02T03:04:05Z",
        "strength": 0.5,
    }
    message = {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "aggregate_type": "signal",
        "aggregate_id": str(uuid4()),
        "version": "1",
        "payload": json.dumps(payload),
    }
    if tenant_id is not None:
        message["tenant_id"] = str(tenant_id)
    return message


def install_consumer(
    monkeypatch: pytest.MonkeyPatch, db: FakePulseDb, redis: Any, max_reads: int
) -> None:
    """Patch the consumer module and stop its loop after ``max_reads`` batch reads."""
    real_read = pulse_consumer.read_consumer_batch
    reads = {"count": 0}

    async def limited_read(*args: Any, **kwargs: Any) -> Any:
        if reads["count"] >= max_reads:
            raise StopLoop
        reads["count"] += 1
        return await real_read(*args, **kwargs)

    monkeypatch.setattr(pulse_consumer, "get_settings", make_settings)
    monkeypatch.setattr(pulse_consumer, "create_engine", lambda *_a, **_k: db)
    monkeypatch.setattr(pulse_consumer, "create_redis", lambda *_a, **_k: RedisProxy(redis))
    monkeypatch.setattr(pulse_consumer, "read_consumer_batch", limited_read)


async def pending_count(redis: Any) -> int:
    info = await redis.xpending(STREAM, pulse_consumer.GROUP_NAME)
    return int(info["pending"])


async def test_repeated_event_is_applied_once(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    db = FakePulseDb()
    message = stream_message()
    for _ in range(3):
        await fake_redis.xadd(STREAM, message)
    install_consumer(monkeypatch, db, fake_redis, max_reads=2)

    with pytest.raises(StopLoop):
        await pulse_consumer.main()

    assert set(db.processed) == {UUID(message["event_id"])}
    assert db.processed[UUID(message["event_id"])]["tenant_id"] == TENANT_ID
    assert len(db.fact_signals) == 1
    assert next(iter(db.fact_signals.values()))["tenant_id"] == TENANT_ID
    assert set(db.tenant_contexts) == {str(TENANT_ID)}
    assert await pending_count(fake_redis) == 0


async def test_other_event_types_are_recorded_but_not_projected() -> None:
    db = FakePulseDb()
    message = stream_message("company.updated")
    async with db.begin() as connection:
        await pulse_consumer.process_event(connection, message)  # type: ignore[arg-type]
    assert set(db.processed) == {UUID(message["event_id"])}
    assert db.fact_signals == {}


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("project.pipeline_updated", {"from": "warm", "to": "hot", "version": 2}),
        ("project.forecast_updated", {"stage": "hot"}),
    ],
)
async def test_stage_events_dispatch_once_under_tenant_context(
    event_type: str, payload: dict[str, Any]
) -> None:
    db = FakePulseDb()
    message = stream_message(event_type)
    body = {"lead_id": str(uuid4()), "project_id": str(uuid4()), **payload}
    message["payload"] = json.dumps(body)
    for _ in range(2):
        async with db.begin() as connection:
            await pulse_consumer.process_event(connection, message)  # type: ignore[arg-type]
    assert [u["stage"] for u in db.stage_updates] == ["hot"]
    assert set(db.tenant_contexts) == {str(TENANT_ID)}


async def test_tenant_id_falls_back_to_payload() -> None:
    db = FakePulseDb()
    message = stream_message(tenant_id=None)
    payload = json.loads(message["payload"])
    payload["tenant_id"] = str(TENANT_ID)
    message["payload"] = json.dumps(payload)
    async with db.begin() as connection:
        await pulse_consumer.process_event(connection, message)  # type: ignore[arg-type]
    assert next(iter(db.fact_signals.values()))["tenant_id"] == TENANT_ID


async def test_event_without_tenant_is_skipped() -> None:
    db = FakePulseDb()
    async with db.begin() as connection:
        await pulse_consumer.process_event(
            connection,  # type: ignore[arg-type]
            stream_message(tenant_id=None),
        )
    assert db.processed == {}
    assert db.fact_signals == {}


async def test_retries_then_acks_after_commit(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    db = FakePulseDb()
    db.fail_next("INSERT INTO pulse.fact_signal", times=1)
    message = stream_message()
    await fake_redis.xadd(STREAM, message)
    install_consumer(monkeypatch, db, fake_redis, max_reads=1)

    with pytest.raises(StopLoop):
        await pulse_consumer.main()

    assert set(db.processed) == {UUID(message["event_id"])}
    assert len(db.fact_signals) == 1
    assert await pending_count(fake_redis) == 0


async def test_message_stays_pending_when_all_attempts_fail(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    db = FakePulseDb()
    db.fail_next("INSERT INTO pulse.fact_signal", times=3)
    await fake_redis.xadd(STREAM, stream_message())
    install_consumer(monkeypatch, db, fake_redis, max_reads=1)

    with pytest.raises(StopLoop):
        await pulse_consumer.main()

    assert db.fact_signals == {}
    assert db.processed == {}
    assert await pending_count(fake_redis) == 1
