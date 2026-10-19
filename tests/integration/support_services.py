"""Helpers for the service-level integration tests (REQ-QA-005, T-078).

Reads ``TEST_DATABASE_URL`` (a server URL; every test module works in its own scratch
database) and ``TEST_REDIS_URL``. ``psycopg`` and ``redis`` are imported only when a
function runs, so collection works without services or the ``sqltest`` extra.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy.engine import make_url

BASE_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BASE_REDIS_URL = os.environ.get("TEST_REDIS_URL")

needs_database = pytest.mark.skipif(not BASE_DATABASE_URL, reason="TEST_DATABASE_URL is not set")
needs_services = pytest.mark.skipif(
    not (BASE_DATABASE_URL and BASE_REDIS_URL),
    reason="TEST_DATABASE_URL and TEST_REDIS_URL are not both set",
)


def asyncpg_url(url: str) -> str:
    """Return the SQLAlchemy asyncpg form of a plain ``postgresql://`` URL."""
    return make_url(url).set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


async def migrate_database(url: str) -> list[str]:
    """Apply ``migrations/`` with the real runner (``ops/migrate.py``); return applied names."""
    import asyncpg  # type: ignore[import-untyped]

    from ops.migrate import migrate

    connection = await asyncpg.connect(url)
    try:
        return await migrate(connection)
    finally:
        await connection.close()


@contextmanager
def migrated_database() -> Iterator[str]:
    """Yield the URL of a scratch database that the runner has already migrated."""
    pytest.importorskip("psycopg")
    pytest.importorskip("asyncpg")
    from support_sql_pg import scratch_database

    assert BASE_DATABASE_URL
    with scratch_database(BASE_DATABASE_URL) as url:
        asyncio.run(migrate_database(url))
        yield url
