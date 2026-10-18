"""Unit tests for ingest budget caps and failed fetch handling (REQ-ING-002).

The pure helpers are tested directly; ``BudgetRepository`` runs against a fake
engine that records SQL, so no Postgres is needed.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from services.ingest.app.budget import (  # noqa: E402
    BudgetExceededError,
    BudgetRepository,
    backoff_seconds,
    budget_period,
    next_failure_state,
    within_budget,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def test_period_is_first_of_month() -> None:
    assert budget_period(NOW) == date(2026, 10, 1)


def test_within_budget_boundaries() -> None:
    assert within_budget(100, 90, 10)
    assert not within_budget(100, 90, 11)
    with pytest.raises(ValueError):
        within_budget(100, 0, -1)


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3)] == [60, 120, 240]
    assert backoff_seconds(30) == 6 * 3600
    with pytest.raises(ValueError):
        backoff_seconds(0)


def test_failures_retry_then_dead_letter_on_fifth() -> None:
    state = next_failure_state(0, NOW)
    assert state.attempts == 1
    assert state.next_attempt_at == NOW + timedelta(seconds=60)
    assert state.dead_lettered_at is None
    fourth = next_failure_state(3, NOW)
    assert fourth.dead_lettered_at is None
    fifth = next_failure_state(4, NOW)
    assert fifth.attempts == 5
    assert fifth.next_attempt_at is None
    assert fifth.dead_lettered_at == NOW


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def scalar_one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def all(self) -> list[Any]:
        return self.rows


class FakeConnection:
    def __init__(self, engine: "FakeEngine") -> None:
        self.engine = engine

    async def execute(self, statement: Any, params: dict[str, Any]) -> FakeResult:
        sql = str(statement)
        self.engine.calls.append((sql, params))
        if "UPDATE ingest.budget" in sql:
            row = self.engine.budget
            key = (params["source"], params["period"])
            if key == row["key"] and row["consumed"] + params["records"] <= row["max"]:
                row["consumed"] += params["records"]
                return FakeResult([row["consumed"]])
            return FakeResult([])
        if "FROM ingest.failed_fetch" in sql and "FOR UPDATE" in sql:
            row = self.engine.failure
            return FakeResult([row] if row and row["dead_lettered_at"] is None else [])
        if "INSERT INTO ingest.failed_fetch" in sql:
            self.engine.failure = {"id": 1, **params}
        elif "UPDATE ingest.failed_fetch" in sql:
            self.engine.failure.update(params)
        return FakeResult([])


class FakeContext:
    def __init__(self, engine: "FakeEngine") -> None:
        self.engine = engine

    async def __aenter__(self) -> FakeConnection:
        return FakeConnection(self.engine)

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeEngine:
    def __init__(self, max_records: int = 100) -> None:
        self.budget = {"key": ("maps", date(2026, 10, 1)), "max": max_records, "consumed": 0}
        self.failure: dict[str, Any] | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def begin(self) -> FakeContext:
        return FakeContext(self)

    connect = begin


def test_reserve_consumes_until_cap_then_blocks() -> None:
    repo = BudgetRepository(FakeEngine(100))
    assert asyncio.run(repo.reserve("maps", NOW, 60)) == 60
    assert asyncio.run(repo.reserve("maps", NOW, 40)) == 100
    with pytest.raises(BudgetExceededError):
        asyncio.run(repo.reserve("maps", NOW, 1))


def test_reserve_blocks_source_without_budget_row() -> None:
    repo = BudgetRepository(FakeEngine())
    with pytest.raises(BudgetExceededError):
        asyncio.run(repo.reserve("computrabajo", NOW, 1))


def test_record_failure_counts_and_dead_letters_after_five() -> None:
    engine = FakeEngine()
    repo = BudgetRepository(engine)
    states = [asyncio.run(repo.record_failure("maps", "snap1", "boom", NOW)) for _ in range(5)]
    assert [s.attempts for s in states] == [1, 2, 3, 4, 5]
    assert states[3].dead_lettered_at is None
    assert states[4].dead_lettered_at == NOW
    assert engine.failure is not None
    assert engine.failure["attempts"] == 5
    assert engine.failure["next_attempt_at"] is None
