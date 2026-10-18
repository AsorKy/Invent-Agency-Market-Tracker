"""Unit tests for the republish command (REQ-EVT-004, REQ-QA-002).

Fakes stand in for the outbox log, the Redis stream, and the lock. Proves ``--confirm`` is
mandatory, events are replayed in order with ``replay: true`` and the target, the lock blocks a
concurrent run and is always released, and the CLI parses its arguments. No services are used.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from services.core.app import republish
from services.core.app.republish import (
    ReplayError,
    build_parser,
    is_target_locked,
    lock_key,
    parse_since,
    replay,
)

SINCE = datetime(2026, 1, 1, tzinfo=UTC)


class FakeLocker:
    def __init__(self) -> None:
        self.held: set[str] = set()
        self.history: list[str] = []

    async def acquire(self, key: str, ttl_seconds: int) -> bool:
        if key in self.held:
            return False
        self.held.add(key)
        self.history.append("acquire")
        return True

    async def release(self, key: str) -> None:
        self.held.discard(key)
        self.history.append("release")

    async def exists(self, key: str) -> bool:
        return key in self.held


def _events(count: int, company: UUID | None = None) -> list[dict[str, Any]]:
    return [
        {
            "id": i,
            "event_id": uuid4(),
            "aggregate_type": "lead",
            "aggregate_id": uuid4(),
            "event_type": "lead.created",
            "version": 1,
            "payload": {"company_id": str(company)} if company else {},
        }
        for i in range(1, count + 1)
    ]


def _run(events: list[dict[str, Any]], locker: FakeLocker, **kw: Any) -> tuple[int, list[Any]]:
    out: list[dict[str, str]] = []
    pages: list[tuple[Any, ...]] = []

    async def fetch(since: datetime, company: UUID | None, after_id: int, limit: int) -> Any:
        pages.append((since, company, after_id))
        return [e for e in events if e["id"] > after_id][:2]

    async def publish(fields: dict[str, str]) -> None:
        assert asyncio.get_event_loop() is not None
        out.append(fields)

    args: dict[str, Any] = {
        "target": "pulse",
        "since": SINCE,
        "company_id": None,
        "confirm": True,
    } | kw
    count = asyncio.run(replay(fetch_page=fetch, publish=publish, locker=locker, **args))
    return count, [out, pages]


def test_replays_all_events_in_order_with_flag_and_target() -> None:
    events = _events(5)
    locker = FakeLocker()
    count, (out, pages) = _run(events, locker, target="brief")
    assert count == 5
    assert [f["event_id"] for f in out] == [str(e["event_id"]) for e in events]
    assert all(f["replay"] == "true" and f["target"] == "brief" for f in out)
    assert [p[2] for p in pages] == [0, 2, 4, 5]
    assert locker.history == ["acquire", "release"] and not locker.held


def test_confirm_and_target_are_required() -> None:
    locker = FakeLocker()
    with pytest.raises(ReplayError, match="confirm"):
        _run(_events(1), locker, confirm=False)
    with pytest.raises(ReplayError, match="target"):
        _run(_events(1), locker, target="gateway")
    assert locker.history == []


def test_lock_blocks_second_run_and_is_released_on_failure() -> None:
    locker = FakeLocker()
    locker.held.add(lock_key("pulse"))
    with pytest.raises(ReplayError, match="already running"):
        _run(_events(1), locker)
    assert lock_key("pulse") in locker.held  # the other run keeps its lock
    locker.held.clear()

    async def boom(fields: dict[str, str]) -> None:
        raise ConnectionError("redis down")

    async def fetch(*_a: Any) -> list[dict[str, Any]]:
        return _events(1)

    with pytest.raises(ConnectionError):
        asyncio.run(
            replay(
                target="ml",
                since=SINCE,
                company_id=None,
                confirm=True,
                fetch_page=fetch,
                publish=boom,
                locker=locker,
            )
        )
    assert not locker.held


def test_target_lock_is_visible_to_consumers() -> None:
    locker = FakeLocker()
    assert asyncio.run(is_target_locked(locker, "ml")) is False
    locker.held.add(lock_key("ml"))
    assert asyncio.run(is_target_locked(locker, "ml")) is True
    assert asyncio.run(is_target_locked(locker, "pulse")) is False


def test_company_filter_is_forwarded() -> None:
    company = uuid4()
    _, (_out, pages) = _run(_events(1, company), FakeLocker(), company_id=company)
    assert pages[0][1] == company


def test_parse_since_and_parser() -> None:
    assert parse_since("2026-01-01") == SINCE
    assert parse_since("2026-01-01T05:00:00+00:00").hour == 5
    with pytest.raises(ReplayError):
        parse_since("yesterday")
    args = build_parser().parse_args(["--target", "ml", "--since", "2026-01-01", "--confirm"])
    assert args.target == "ml" and args.confirm is True and args.company_id is None
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--target", "gateway", "--since", "2026-01-01"])


def test_cli_refuses_without_confirm(capsys: pytest.CaptureFixture[str]) -> None:
    code = asyncio.run(republish.run(["--target", "pulse", "--since", "2026-01-01"]))
    assert code == 2
    assert "--confirm" in capsys.readouterr().err
    assert asyncio.run(republish.run(["--target", "pulse", "--since", "nope", "--confirm"])) == 2
