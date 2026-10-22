"""PostgreSQL helpers for the SQL integration tier (decision D-18, T-095).

Creates a scratch database next to the one named by ``TEST_DATABASE_URL``,
applies ``migrations/`` in order, prepares statements without running them, and
reads row level security state. ``psycopg`` is imported only when a function
runs, so ``make test`` works without the ``sqltest`` extra.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[2]
_BIND = re.compile(r"'(?:[^']|'')*'|--[^\n]*|\"[^\"]*\"|::|:([A-Za-z_]\w*)")


def numbered_binds(sql: str) -> tuple[str, int]:
    """Replace ``:name`` binds with ``$n``; return the SQL and the bind count."""
    numbers: dict[str, int] = {}

    def swap(match: re.Match[str]) -> str:
        name = match.group(1)
        if name is None:
            return match.group(0)
        return f"${numbers.setdefault(name, len(numbers) + 1)}"

    return _BIND.sub(swap, sql), len(numbers)


def scratch_url(base_url: str, name: str) -> str:
    """Return ``base_url`` with its database name replaced by ``name``.

    The result stays a URL: asyncpg rejects the ``key=value`` form that psycopg builds.
    """
    return make_url(base_url).set(database=name).render_as_string(hide_password=False)


@contextmanager
def scratch_database(base_url: str) -> Iterator[str]:
    """Create a throwaway database, yield its URL, and drop it afterwards."""
    import psycopg
    from psycopg import sql

    name = f"imt_sql_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield scratch_url(base_url, name)
    finally:
        with psycopg.connect(base_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def apply_migrations(conn: Any, files: list[str]) -> None:
    """Run every migration file in the given order, each in its own transaction."""
    for name in files:
        text = (REPO / name).read_text(encoding="utf-8")
        with conn.transaction():
            conn.execute(text)


def prepare_and_release(conn: Any, index: int, sql_text: str) -> None:
    """PREPARE a statement (server-side check only), then DEALLOCATE it.

    The caller runs this inside a transaction that is rolled back, so a failed
    PREPARE never poisons later statements.
    """
    name = f"stmt_{index}"
    conn.execute(f"PREPARE {name} AS {sql_text}")
    conn.execute(f"DEALLOCATE {name}")


def tables_without_rls(conn: Any) -> list[str]:
    """Tables with a ``tenant_id`` column that do not have row level security."""
    rows = conn.execute(
        """
        SELECT n.nspname || '.' || c.relname
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE a.attname = 'tenant_id' AND NOT a.attisdropped
          AND c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND NOT c.relrowsecurity
        ORDER BY 1
        """
    ).fetchall()
    return [row[0] for row in rows]


def tables_with_tenant_id(conn: Any) -> list[str]:
    """All user tables that have a ``tenant_id`` column."""
    rows = conn.execute(
        """
        SELECT n.nspname || '.' || c.relname
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE a.attname = 'tenant_id' AND NOT a.attisdropped
          AND c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY 1
        """
    ).fetchall()
    return [row[0] for row in rows]


def role_url(base_url: str, role: str, password: str) -> str:
    """Return ``base_url`` with the user and password replaced (keeps the URL form)."""
    return (
        make_url(base_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )


def bootstrap_app_roles(url: str, password: str) -> dict[str, str]:
    """Create the owner and app roles on the migrated database; map schema to app role name.

    Skips the calling test when the server user may not create roles (not a superuser).
    """
    import psycopg

    from ops.bootstrap_roles import generate_sql
    from ops.schemas import SCHEMAS, app_role

    try:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(
                generate_sql(passwords={schema: password for schema in SCHEMAS}, extensions=())
            )
    except psycopg.errors.InsufficientPrivilege:
        import pytest

        pytest.skip("the test user cannot create roles or change schema owners")
    return {schema: app_role(schema) for schema in SCHEMAS}


def admin_execute(url: str, sql_text: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Run one statement as the server user (outside row level security); return its rows."""
    import psycopg

    with psycopg.connect(url, autocommit=True) as connection:
        cursor = connection.execute(sql_text, params)
        return cursor.fetchall() if cursor.description else []
