"""Provide scripted fake SQL connections, engines and a Redis ``eval`` stub for coverage tests.

The fakes replace the small part of the SQLAlchemy async API used by
``services/core/app/repository.py`` and ``libs/common/db.py``. They relate to
``tests/unit/test_cov_infra.py``. ``FakeConnection`` records every statement and
parameter set and answers from a queue of scripted results, so SQL paths run
without Postgres.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any


class FakeResult:
    """Imitate the result object returned by ``AsyncConnection.execute``."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        scalar: Any = None,
        tuples: list[tuple[Any, ...]] | None = None,
        rowcount: int = 0,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self._tuples = tuples if tuples is not None else [tuple(r.values()) for r in self._rows]
        self.rowcount = rowcount

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> Any:
        if self._rows:
            return self._rows[0]
        return self._tuples[0] if self._tuples else None

    def all(self) -> list[Any]:
        return list(self._rows) if self._rows else list(self._tuples)

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalar_one(self) -> Any:
        if self._scalar is None:
            raise LookupError("no scalar scripted")
        return self._scalar


class FakeConnection:
    """Record statements and answer from a queue of scripted results."""

    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self.results = list(results or [])
        self.routes: dict[str, list[FakeResult]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.nested = 0

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params or {}))
        for marker, queued in self.routes.items():
            if marker in sql and queued:
                return queued.pop(0)
        return self.results.pop(0) if self.results else FakeResult()

    async def exec_driver_sql(self, statement: str) -> FakeResult:
        self.calls.append((statement, {}))
        return FakeResult()

    @asynccontextmanager
    async def begin_nested(self) -> AsyncIterator[None]:
        self.nested += 1
        yield

    def route(self, marker: str, *results: FakeResult) -> None:
        """Answer statements containing ``marker`` from their own queue, in order."""
        self.routes.setdefault(marker, []).extend(results)

    @property
    def sql(self) -> str:
        return "\n".join(call[0] for call in self.calls)


class _Context:
    def __init__(self, connection: FakeConnection, log: list[str], kind: str) -> None:
        self._connection = connection
        self._log = log
        self._kind = kind

    async def __aenter__(self) -> FakeConnection:
        self._log.append(f"enter:{self._kind}")
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._log.append(f"exit:{self._kind}:{'error' if exc_type else 'ok'}")


class FakeEngine:
    """Hand out one shared ``FakeConnection`` for ``begin`` and ``connect``."""

    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self.connection = FakeConnection(results)
        self.log: list[str] = []
        self.disposed = False

    def begin(self) -> _Context:
        return _Context(self.connection, self.log, "begin")

    def connect(self) -> _Context:
        return _Context(self.connection, self.log, "connect")

    async def dispose(self) -> None:
        self.disposed = True


class FakeEvalRedis:
    """Return a scripted answer from ``eval`` and remember the call arguments."""

    def __init__(self, answer: list[Any]) -> None:
        self.answer = answer
        self.calls: list[tuple[Any, ...]] = []

    async def eval(self, *args: Any) -> list[Any]:
        self.calls.append(args)
        return self.answer
