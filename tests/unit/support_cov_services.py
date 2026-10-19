"""Provide scripted SQL fakes and stream stubs for the service coverage tests.

The fakes imitate the small part of the SQLAlchemy async API that the ingest, ml,
pulse and core repositories use. They relate to ``tests/unit/test_cov_services.py``.
``FakeEngine`` hands out connections that record each statement with its parameters
and answer from a queue of scripted results, so SQL code paths run without Postgres.
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
        rowcount: int = 0,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, Any]:
        return self._rows[0]

    def all(self) -> list[Any]:
        return [tuple(r.values()) for r in self._rows]

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalar_one(self) -> Any:
        return self._scalar


class FakeConnection:
    """Record statements and answer from a queue of scripted results."""

    def __init__(self, script: list[FakeResult] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.calls.append((" ".join(str(statement).split()), params or {}))
        return self.script.pop(0) if self.script else FakeResult()


class FakeEngine:
    """Return one shared ``FakeConnection`` from ``begin`` and ``connect``."""

    def __init__(self, script: list[FakeResult] | None = None) -> None:
        self.connection = FakeConnection(script)
        self.disposed = False

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeConnection]:
        yield self.connection

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[FakeConnection]:
        yield self.connection

    async def dispose(self) -> None:
        self.disposed = True


class FakeRedisWrapper:
    """Wrap a fake Redis client with a failing ``xadd`` and a no-op ``aclose``."""

    def __init__(self, inner: Any, *, fail_xadd: bool = False) -> None:
        self._inner = inner
        self._fail_xadd = fail_xadd
        self.closed = False

    async def xadd(self, *args: Any, **kwargs: Any) -> Any:
        if self._fail_xadd:
            raise ConnectionError("simulated redis failure")
        return await self._inner.xadd(*args, **kwargs)

    async def eval(self, _script: str, _numkeys: int, key: str, owner: str) -> int:
        """Imitate the release-lock script: delete the key when the owner matches."""
        if await self._inner.get(key) == owner:
            return int(await self._inner.delete(key))
        return 0

    async def aclose(self) -> None:
        self.closed = True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class Stop(BaseException):
    """Break an endless worker loop (not caught by ``except Exception``)."""

    def __init__(self, *args: object) -> None:
        super().__init__(*args)


class AsyncExit:
    """Async context manager that records entry and exit."""

    def __init__(self, value: Any) -> None:
        self.value = value
        self.exited = False

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.exited = True
