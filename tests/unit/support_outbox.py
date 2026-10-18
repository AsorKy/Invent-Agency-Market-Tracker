"""Provide in-memory fakes for the outbox relay and stream consumer tests.

The fakes imitate the small part of the SQLAlchemy async engine and the Redis
client that ``outbox_relay.py`` and the ``ml`` and ``analytics`` consumers use.
They relate to ``tests/unit/test_outbox.py`` and dispatch on SQL text, so no
Postgres or Redis server is needed. Transactions stage writes and commit them
only on clean exit, which lets tests check rollback plus retry behavior.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import UUID


class StopLoop(BaseException):
    """Signal that a worker's endless loop should end (not caught by ``except Exception``)."""


def make_settings() -> SimpleNamespace:
    """Return worker settings with zero delays so retries run instantly."""
    return SimpleNamespace(
        database_url="postgresql://fake/db",
        redis_url="redis://fake/0",
        worker_retry_attempts=3,
        worker_retry_base_delay_seconds=0.0,
        worker_retry_max_delay_seconds=0.0,
        worker_retry_jitter_ratio=0.0,
        outbox_poll_interval_seconds=0,
        worker_stream_block_ms=1,
        worker_failure_sleep_seconds=0,
    )


class RedisProxy:
    """Wrap a fake Redis client: no-op ``aclose`` and optional ``xadd`` failures."""

    def __init__(self, inner: Any, fail_xadd_on_calls: set[int] | None = None) -> None:
        self._inner = inner
        self._fail_on = fail_xadd_on_calls or set()
        self.xadd_calls = 0

    async def xadd(self, *args: Any, **kwargs: Any) -> Any:
        self.xadd_calls += 1
        if self.xadd_calls in self._fail_on:
            raise ConnectionError("simulated redis failure")
        return await self._inner.xadd(*args, **kwargs)

    async def aclose(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _Transaction:
    def __init__(self, db: "FakeDb", writable: bool) -> None:
        self._db = db
        self._writable = writable
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


class FakeDb:
    """Dispatch the exact statements the workers issue and keep committed state."""

    def __init__(self, outbox_rows: list[dict[str, Any]] | None = None) -> None:
        self.outbox_rows = outbox_rows or []
        self.published: list[int] = []
        self.processed: dict[str, set[UUID]] = {"ml": set(), "analytics": set()}
        self.stale_updates: list[UUID] = []
        self.fact_signals: dict[UUID, dict[str, Any]] = {}
        self.fail_statements: dict[str, int] = {}
        self.disposed = False

    def fail_next(self, marker: str, times: int = 1) -> None:
        """Make the next ``times`` statements containing ``marker`` raise."""
        self.fail_statements[marker] = times

    def connect(self) -> _Transaction:
        return _Transaction(self, writable=False)

    def begin(self) -> _Transaction:
        return _Transaction(self, writable=True)

    async def dispose(self) -> None:
        self.disposed = True

    def dispatch(
        self, sql: str, params: dict[str, Any], staged: list[Callable[[], None]]
    ) -> _Result:
        for marker, remaining in list(self.fail_statements.items()):
            if remaining > 0 and marker in sql:
                self.fail_statements[marker] = remaining - 1
                raise ConnectionError("simulated database failure")
        if "FROM core.outbox_event" in sql:
            pending = [r for r in self.outbox_rows if r["id"] not in self.published]
            return _Result(rows=pending[:100])
        if "UPDATE core.outbox_event SET published_at" in sql:
            staged.append(lambda: self.published.append(params["id"]))
            return _Result()
        for schema in ("ml", "analytics"):
            if f"INSERT INTO {schema}.processed_event" in sql:
                event_id = params["event_id"]
                if event_id in self.processed[schema]:
                    return _Result(scalar=None)
                staged.append(lambda s=schema, e=event_id: self.processed[s].add(e))
                return _Result(scalar=event_id)
        if "UPDATE ml.lead_score" in sql:
            staged.append(lambda: self.stale_updates.append(params["company_id"]))
            return _Result()
        if "INSERT INTO analytics.fact_signal" in sql:
            staged.append(lambda: self.fact_signals.setdefault(params["signal_id"], params))
            return _Result()
        raise AssertionError(f"unexpected SQL: {sql}")
