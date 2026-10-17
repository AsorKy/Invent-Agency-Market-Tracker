"""Create shared asynchronous PostgreSQL connections.

The module builds the engine and session factory from ``DATABASE_URL`` and
provides a lightweight health check. It relates to ``libs.common.settings``
for configuration and to the repositories under ``services/*/app`` for reads
and writes; the required tables are created by
``migrations/20261017_0001_expand_initial_schemas.sql``. Its purpose is to keep
SQLAlchemy configuration in one place and remain PgBouncer compatible by
disabling prepared statement caches.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str | None, *, pool_size: int = 4) -> AsyncEngine | None:
    if not database_url:
        return None
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_timeout=5,
        pool_size=pool_size,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
    )


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
