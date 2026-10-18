"""Create shared asynchronous PostgreSQL connections.

The module builds the engine and session factory from ``DATABASE_URL`` and
provides a lightweight health check. It relates to ``libs.common.settings``
for configuration and to the repositories under ``services/*/app`` for reads
and writes; the additive database foundation is defined in
``migrations/20261017_0002_arch_v22_foundations.sql``. Its purpose is to keep
SQLAlchemy pool settings in one place and remain compatible with transaction
poolers by disabling both asyncpg prepared-statement caches.
"""

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from libs.common.settings import get_settings


def create_engine(database_url: str | None, *, pool_size: int | None = None) -> AsyncEngine | None:
    if not database_url:
        return None
    settings = get_settings()
    pool_options = {
        "pool_pre_ping": True,
        "pool_timeout": settings.database_pool_timeout_seconds,
        "pool_size": pool_size or settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
    }
    connect_args: dict[str, object] = {"timeout": settings.database_connect_timeout_seconds}

    # ``statement_cache_size`` is an asyncpg connection option. The
    # ``prepared_statement_cache_size`` option is translated by SQLAlchemy's
    # asyncpg dialect from the URL query string into the adapted DBAPI connect
    # call. Keep it in the URL so SQLAlchemy does not forward it to asyncpg.
    if make_url(database_url).drivername == "postgresql+asyncpg":
        connect_args["statement_cache_size"] = 0
        database_url_object = make_url(database_url).update_query_dict(
            {"prepared_statement_cache_size": "0"}
        )
        database_url = database_url_object.render_as_string(hide_password=False)
        return create_async_engine(
            database_url,
            **pool_options,
            connect_args=connect_args,
        )

    # Keep the existing behavior for any other async dialect URL. The URL
    # still determines the driver, while the shared timeout and pool limits
    # remain explicit.
    return create_async_engine(database_url, **pool_options, connect_args=connect_args)


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
