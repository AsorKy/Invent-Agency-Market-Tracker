"""Unit tests for the outbox retry schedule and dead letter queue (REQ-EVT-003, REQ-QA-002).

A fake unit of work holds outbox rows and applies the same bookkeeping as the SQL version. The
tests drive ``relay_due_events`` with a clock to prove the 10 s, 30 s, 90 s, 5 min, 15 min
schedule, the move to the dead letter table with an alert log line, and per-aggregate ordering.
No database or Redis is needed.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import uuid4

import pytest

from services.core.app.dead_letter import (
    BACKOFF_SECONDS,
    relay_due_events,
    retry_delay,
    stream_fields,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
TENANT = uuid4()


class Outbox:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.dead: list[dict[str, Any]] = []


class FakeUow:
    def __init__(self, outbox: Outbox) -> None:
        self.o = outbox

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def claim_due(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        due = []
        for row in self.o.rows:
            if row.get("published_at") or row.get("dead_lettered_at"):
                continue
            if row.get("next_attempt_at") and row["next_attempt_at"] > now:
                continue
            held = any(
                p["aggregate_id"] == row["aggregate_id"]
                and p["id"] < row["id"]
                and not p.get("published_at")
                and not p.get("dead_lettered_at")
                and p.get("next_attempt_at")
                and p["next_attempt_at"] > now
                for p in self.o.rows
            )
            if not held:
                due.append(dict(row))
        return due[:limit]

    def _row(self, row_id: int) -> dict[str, Any]:
        return next(r for r in self.o.rows if r["id"] == row_id)

    async def mark_published(self, row_id: int, now: datetime) -> None:
        self._row(row_id)["published_at"] = now

    async def record_failure(
        self, row_id: int, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None:
        self._row(row_id).update(
            attempts=attempts, next_attempt_at=next_attempt_at, last_error=error
        )

    async def dead_letter(
        self, row: dict[str, Any], *, attempts: int, error: str, now: datetime
    ) -> None:
        self.o.dead.append({"event_id": row["event_id"], "attempts": attempts, "error": error})
        self._row(row["id"]).update(dead_lettered_at=now, attempts=attempts, last_error=error)


def _row(row_id: int, aggregate_id: Any = None) -> dict[str, Any]:
    return {
        "id": row_id,
        "event_id": uuid4(),
        "tenant_id": TENANT,
        "aggregate_type": "lead",
        "aggregate_id": aggregate_id or uuid4(),
        "event_type": "lead.created",
        "version": 1,
        "payload": {"k": "v"},
        "attempts": 0,
        "replay": False,
    }


def _pass(outbox: Outbox, fail: set[Any], now: datetime, published: list[Any] | None = None):
    async def publish(row: dict[str, Any]) -> None:
        if row["id"] in fail:
            raise ConnectionError("redis down")
        if published is not None:
            published.append(row["id"])

    return asyncio.run(relay_due_events(FakeUow(outbox), publish, now=now))


def test_schedule_matches_the_adr() -> None:
    assert BACKOFF_SECONDS == (10, 30, 90, 300, 900)
    assert [retry_delay(n) for n in range(1, 6)] == [
        timedelta(seconds=s) for s in (10, 30, 90, 300, 900)
    ]
    assert retry_delay(0) is None and retry_delay(6) is None


def test_success_marks_published() -> None:
    outbox = Outbox([_row(1)])
    seen: list[Any] = []
    assert _pass(outbox, set(), T0, seen) == {"published": 1, "retried": 0, "dead_lettered": 0}
    assert seen == [1] and outbox.rows[0]["published_at"] == T0
    assert _pass(outbox, set(), T0, seen)["published"] == 0


def test_failures_follow_backoff_then_dead_letter_with_alert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    outbox = Outbox([_row(1)])
    now = T0
    for attempt, seconds in enumerate(BACKOFF_SECONDS, start=1):
        assert _pass(outbox, {1}, now)["retried"] == 1
        row = outbox.rows[0]
        assert row["attempts"] == attempt
        assert row["next_attempt_at"] == now + timedelta(seconds=seconds)
        assert "ConnectionError" in row["last_error"]
        # Not due yet: nothing is attempted one second early.
        assert _pass(outbox, {1}, row["next_attempt_at"] - timedelta(seconds=1)) == {
            "published": 0,
            "retried": 0,
            "dead_lettered": 0,
        }
        now = row["next_attempt_at"]
    with caplog.at_level(logging.ERROR):
        result = _pass(outbox, {1}, now)
    assert result["dead_lettered"] == 1
    assert outbox.dead[0]["attempts"] == 6 and "redis down" in outbox.dead[0]["error"]
    assert outbox.rows[0]["dead_lettered_at"] == now
    assert any("ALERT" in r.message and "lead.created" in r.message for r in caplog.records)
    assert "payload" not in " ".join(r.message for r in caplog.records)
    assert _pass(outbox, set(), now + timedelta(days=1))["published"] == 0


def test_recovery_after_a_failure_publishes() -> None:
    outbox = Outbox([_row(1)])
    _pass(outbox, {1}, T0)
    seen: list[Any] = []
    assert _pass(outbox, set(), T0 + timedelta(seconds=10), seen)["published"] == 1
    assert seen == [1]


def test_failed_aggregate_keeps_order_while_others_flow() -> None:
    shared = uuid4()
    outbox = Outbox([_row(1, shared), _row(2, shared), _row(3)])
    seen: list[Any] = []
    result = _pass(outbox, {1}, T0, seen)
    assert result == {"published": 1, "retried": 1, "dead_lettered": 0}
    assert seen == [3]
    # Event 2 is held back while 1 waits for its retry.
    assert _pass(outbox, set(), T0 + timedelta(seconds=5), seen)["published"] == 0
    assert _pass(outbox, set(), T0 + timedelta(seconds=10), seen)["published"] == 2
    assert seen == [3, 1, 2]


def test_stream_fields_mark_replays_only_when_asked() -> None:
    row = _row(1)
    plain = stream_fields(row)
    assert "replay" not in plain and plain["payload"] == '{"k": "v"}'
    replayed = stream_fields(row, replay=True, target="ml")
    assert replayed["replay"] == "true" and replayed["target"] == "ml"
