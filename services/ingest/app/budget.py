"""Monthly budget caps and failed fetch handling for ingestion (REQ-ING-002).

Pure helpers (period, cap check, exponential backoff, dead letter decision) have
no I/O. ``BudgetRepository`` applies them to ``ingest.budget`` and
``ingest.failed_fetch`` through an injected SQLAlchemy async engine. Callers
must call ``reserve`` before any Bright Data trigger. It relates to
``repository.py`` and the ``ingest`` schema from migration 0001.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

MAX_FAILURES = 5
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 6 * 3600


class BudgetExceededError(Exception):
    """Raised when a trigger would go beyond the monthly cap of a source."""


@dataclass(frozen=True)
class FailureState:
    """Next state of a failed fetch after one more failure."""

    attempts: int
    next_attempt_at: datetime | None
    dead_lettered_at: datetime | None


def budget_period(now: datetime | date) -> date:
    """Return the first day of the month, the ``period`` key of ``ingest.budget``."""
    return date(now.year, now.month, 1)


def within_budget(max_records: int, consumed: int, requested: int) -> bool:
    """Return True when ``requested`` more records fit in the cap."""
    if requested < 0:
        raise ValueError("requested records must not be negative")
    return consumed + requested <= max_records


def backoff_seconds(
    attempts: int,
    *,
    base_seconds: int = BACKOFF_BASE_SECONDS,
    cap_seconds: int = BACKOFF_CAP_SECONDS,
) -> int:
    """Exponential delay after ``attempts`` failures: base * 2^(attempts-1), capped."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    return int(min(base_seconds * 2 ** (attempts - 1), cap_seconds))


def next_failure_state(
    previous_attempts: int,
    now: datetime,
    *,
    max_failures: int = MAX_FAILURES,
    base_seconds: int = BACKOFF_BASE_SECONDS,
    cap_seconds: int = BACKOFF_CAP_SECONDS,
) -> FailureState:
    """Compute attempts, retry time, and dead letter time after a new failure."""
    attempts = previous_attempts + 1
    if attempts >= max_failures:
        return FailureState(attempts, None, now)
    delay = backoff_seconds(attempts, base_seconds=base_seconds, cap_seconds=cap_seconds)
    return FailureState(attempts, now + timedelta(seconds=delay), None)


class BudgetRepository:
    """SQL access for budgets and failed fetches; the engine is injected."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    async def set_cap(self, source: str, now: datetime, max_records: int) -> None:
        """Create or update the monthly cap for a source."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ingest.budget (source, period, max_records)
                    VALUES (:source, :period, :max_records)
                    ON CONFLICT (source, period) DO UPDATE SET max_records = :max_records
                    """
                ),
                {"source": source, "period": budget_period(now), "max_records": max_records},
            )

    async def remaining(self, source: str, now: datetime) -> int:
        """Return the records left in this month's cap; 0 without a budget row."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT max_records - consumed FROM ingest.budget
                    WHERE source = :source AND period = :period
                    """
                ),
                {"source": source, "period": budget_period(now)},
            )
            left = result.scalar_one_or_none()
        return max(int(left), 0) if left is not None else 0

    async def reserve(self, source: str, now: datetime, records: int) -> int:
        """Atomically consume ``records`` of the cap or raise ``BudgetExceededError``.

        A source without a budget row for the month is blocked (fail closed).
        Returns the consumed total after the reservation.
        """
        if records < 0:
            raise ValueError("records must not be negative")
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE ingest.budget SET consumed = consumed + :records
                    WHERE source = :source AND period = :period
                      AND consumed + :records <= max_records
                    RETURNING consumed
                    """
                ),
                {"source": source, "period": budget_period(now), "records": records},
            )
            consumed = result.scalar_one_or_none()
        if consumed is None:
            raise BudgetExceededError(f"monthly budget exceeded or missing for source {source}")
        return int(consumed)

    async def record_failure(
        self, source: str, external_id: str | None, error: str, now: datetime
    ) -> FailureState:
        """Store a failed fetch, update backoff, and dead letter after 5 failures."""
        params = {"source": source, "external_id": external_id}
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, attempts FROM ingest.failed_fetch
                    WHERE source = :source AND external_id IS NOT DISTINCT FROM :external_id
                      AND dead_lettered_at IS NULL
                    ORDER BY id DESC LIMIT 1
                    FOR UPDATE
                    """
                ),
                params,
            )
            row = result.mappings().first()
            state = next_failure_state(row["attempts"] if row else 0, now)
            values = {
                "error": error,
                "attempts": state.attempts,
                "next_attempt_at": state.next_attempt_at,
                "dead_lettered_at": state.dead_lettered_at,
            }
            if row:
                await connection.execute(
                    text(
                        """
                        UPDATE ingest.failed_fetch
                        SET error = :error, attempts = :attempts,
                            next_attempt_at = :next_attempt_at,
                            dead_lettered_at = :dead_lettered_at
                        WHERE id = :id
                        """
                    ),
                    {**values, "id": row["id"]},
                )
            else:
                await connection.execute(
                    text(
                        """
                        INSERT INTO ingest.failed_fetch
                            (source, external_id, error, attempts, next_attempt_at,
                             dead_lettered_at)
                        VALUES (:source, :external_id, :error, :attempts, :next_attempt_at,
                                :dead_lettered_at)
                        """
                    ),
                    {**params, **values},
                )
        return state

    async def due_retries(
        self, source: str, now: datetime, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return failed fetches whose backoff elapsed and are not dead lettered."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, external_id, attempts FROM ingest.failed_fetch
                    WHERE source = :source AND dead_lettered_at IS NULL
                      AND next_attempt_at <= :now
                    ORDER BY next_attempt_at LIMIT :limit
                    """
                ),
                {"source": source, "now": now, "limit": limit},
            )
            return [dict(row) for row in result.mappings().all()]
