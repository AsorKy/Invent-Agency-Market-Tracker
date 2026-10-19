"""Migrations and role isolation on a real PostgreSQL (REQ-QA-005, REQ-DAT-007, T-078).

Skipped unless ``TEST_DATABASE_URL`` is set (a server URL with rights to create databases).
The role test also needs the right to create roles and to change schema owners (a superuser);
it skips itself when the server refuses. Needs the ``sqltest`` extra (``psycopg[binary]``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from support_services import (
    BASE_DATABASE_URL,
    migrate_database,
    needs_database,
)

pytestmark = needs_database

REPO = Path(__file__).resolve().parents[2]
ROLE_PASSWORD = "imt-integration-only"


async def test_runner_applies_every_migration_to_an_empty_database_once() -> None:
    pytest.importorskip("asyncpg")
    psycopg = pytest.importorskip("psycopg")
    from support_sql_pg import scratch_database

    from ops.migrate import list_migrations
    from ops.schemas import SCHEMAS

    expected = [path.name for path in list_migrations()]
    assert BASE_DATABASE_URL
    with scratch_database(BASE_DATABASE_URL) as url:
        assert await migrate_database(url) == expected
        assert await migrate_database(url) == []
        with psycopg.connect(url, autocommit=True) as connection:
            recorded = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM public.schema_migration ORDER BY version"
                )
            ]
            schemas = {
                row[0]
                for row in connection.execute("SELECT schema_name FROM information_schema.schemata")
            }
    assert sorted(recorded) == sorted(expected)
    assert set(SCHEMAS) <= schemas


async def test_runner_refuses_a_migration_that_changed_after_it_was_applied(
    tmp_path: Path,
) -> None:
    pytest.importorskip("asyncpg")
    import asyncpg  # type: ignore[import-untyped]
    from support_sql_pg import scratch_database

    from ops.migrate import ChecksumError, migrate

    copy = tmp_path / "migrations"
    shutil.copytree(REPO / "migrations", copy, ignore=shutil.ignore_patterns("*.md"))
    assert BASE_DATABASE_URL
    with scratch_database(BASE_DATABASE_URL) as url:
        connection = await asyncpg.connect(url)
        try:
            await migrate(connection, copy)
            first = sorted(copy.glob("*.sql"))[0]
            first.write_text(first.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
            with pytest.raises(ChecksumError):
                await migrate(connection, copy)
        finally:
            await connection.close()


@pytest.fixture(scope="module")
def app_role_urls(database_url: str) -> dict[str, str]:
    """Run the role bootstrap on the migrated database; map each schema to its app URL."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from ops.bootstrap_roles import generate_sql
    from ops.schemas import SCHEMAS, app_role

    passwords = {schema: ROLE_PASSWORD for schema in SCHEMAS}
    try:
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(generate_sql(passwords=passwords, extensions=()))  # type: ignore[arg-type]
    except psycopg.errors.InsufficientPrivilege:
        pytest.skip("the test user cannot create roles or change schema owners")
    base = conninfo_to_dict(database_url)
    return {
        schema: make_conninfo(**{**base, "user": app_role(schema), "password": ROLE_PASSWORD})
        for schema in SCHEMAS
    }


def _first_table(database_url: str, schema: str) -> str | None:
    import psycopg

    with psycopg.connect(database_url, autocommit=True) as connection:
        row = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name LIMIT 1",
            (schema,),
        ).fetchone()
    return row[0] if row else None


def test_each_app_role_reads_its_own_schema_and_no_other(
    database_url: str, app_role_urls: dict[str, str]
) -> None:
    import psycopg

    tables = {schema: _first_table(database_url, schema) for schema in app_role_urls}
    owned = {schema: table for schema, table in tables.items() if table}
    assert {"identity", "ingest", "core", "pulse", "brief", "ml"} <= set(owned)
    leaks: list[str] = []
    for schema, url in app_role_urls.items():
        if schema not in owned:
            continue
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(f"SELECT 1 FROM {schema}.{owned[schema]} LIMIT 0")
            for other, table in owned.items():
                if other == schema:
                    continue
                try:
                    connection.execute(f"SELECT 1 FROM {other}.{table} LIMIT 0")
                except psycopg.errors.InsufficientPrivilege:
                    continue
                leaks.append(f"{schema}_app can read {other}.{table}")
    assert leaks == []


def test_app_roles_cannot_create_objects_in_foreign_schemas(
    app_role_urls: dict[str, str],
) -> None:
    import psycopg

    for schema, url in app_role_urls.items():
        with psycopg.connect(url, autocommit=True) as connection:
            for other in app_role_urls:
                if other == schema:
                    continue
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(f"CREATE TABLE {other}.imt_isolation_probe (id int)")
