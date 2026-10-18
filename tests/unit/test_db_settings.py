"""Assert pooler-safe driver arguments without connecting (REQ-DAT-004)."""

import pytest

pytest.importorskip("sqlalchemy")

from libs.common.db import engine_settings  # noqa: E402

COMMON = {"pool_size": 4, "max_overflow": 0, "pool_timeout": 5, "connect_timeout": 5}


def test_asyncpg_arguments() -> None:
    url, options = engine_settings("postgresql+asyncpg://u:p@h:6543/db", **COMMON)
    assert "prepared_statement_cache_size=0" in url
    assert options["connect_args"]["statement_cache_size"] == 0
    assert options["connect_args"]["timeout"] == 5
    assert options["pool_size"] == 4
    assert options["pool_pre_ping"] is True
    assert options["pool_timeout"] == 5
    assert options["max_overflow"] == 0


def test_asyncpg_keeps_password_and_existing_query() -> None:
    url, _ = engine_settings("postgresql+asyncpg://u:p%40@h/db?ssl=require", **COMMON)
    assert "ssl=require" in url
    assert "p%40" in url


def test_psycopg_arguments() -> None:
    url, options = engine_settings("postgresql+psycopg://u:p@h:6543/db", **COMMON)
    assert url == "postgresql+psycopg://u:p@h:6543/db"
    assert options["connect_args"]["prepare_threshold"] is None
    assert "prepare_threshold" in options["connect_args"]
    assert options["pool_size"] == 2
    assert options["pool_pre_ping"] is True
    assert options["pool_timeout"] == 5


def test_create_engine_without_url_returns_none() -> None:
    pytest.importorskip("pydantic_settings")
    from libs.common.db import create_engine

    assert create_engine(None) is None
