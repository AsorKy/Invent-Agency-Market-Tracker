"""Create shared asynchronous PostgreSQL connections.

The module builds the engine and session factory from ``DATABASE_URL`` and
provides a lightweight health check. It relates to ``libs.common.settings``
for configuration and to the repositories under ``services/*/app`` for reads
and writes; the additive database foundation is defined in
``migrations/20261017_0002_arch_v22_foundations.sql``. Its purpose is to keep
SQLAlchemy pool settings in one place and remain compatible with transaction
poolers by disabling both asyncpg prepared-statement caches.
"""

from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from libs.common.settings import get_settings

ASYNCPG_DRIVER = "postgresql+asyncpg"
PSYCOPG_DRIVER = "postgresql+psycopg"
PSYCOPG_POOL_SIZE = 2


def engine_settings(
    database_url: str,
    *,
    pool_size: int,
    max_overflow: int,
    pool_timeout: float,
    connect_timeout: float,
) -> tuple[str, dict[str, Any]]:
    """Return the final URL and ``create_async_engine`` kwargs without connecting.

    asyncpg: ``statement_cache_size=0`` in connect_args and
    ``prepared_statement_cache_size=0`` in the URL query (SQLAlchemy consumes it).
    psycopg 3: ``prepare_threshold=None`` and a pool of ``PSYCOPG_POOL_SIZE``.
    """

    url = make_url(database_url)
    options: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_timeout": pool_timeout,
        "max_overflow": max_overflow,
    }
    if url.drivername == ASYNCPG_DRIVER:
        options["pool_size"] = pool_size
        options["connect_args"] = {"timeout": connect_timeout, "statement_cache_size": 0}
        url = url.update_query_dict({"prepared_statement_cache_size": "0"})
        return url.render_as_string(hide_password=False), options
    if url.drivername == PSYCOPG_DRIVER:
        options["pool_size"] = PSYCOPG_POOL_SIZE
        options["connect_args"] = {
            "connect_timeout": max(1, int(connect_timeout)),
            "prepare_threshold": None,
        }
        return database_url, options
    # Other async dialects keep the shared timeout and pool limits explicit.
    options["pool_size"] = pool_size
    options["connect_args"] = {"timeout": connect_timeout}
    return database_url, options


def create_engine(database_url: str | None, *, pool_size: int | None = None) -> AsyncEngine | None:
    if not database_url:
        return None
    settings = get_settings()
    url, options = engine_settings(
        database_url,
        pool_size=pool_size or settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        connect_timeout=settings.database_connect_timeout_seconds,
    )
    return create_async_engine(url, **options)


def session_factory(engine: AsyncEngine | None) -> async_sessionmaker[AsyncSession] | None:
    if engine is None:
        return None
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_database(database_url: str | None) -> bool:
    engine = create_engine(database_url, pool_size=1)
    if engine is None:
        return False
    try:
        async with engine.connect() as connection:
            await connection.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()
