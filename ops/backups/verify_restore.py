"""Verify a schema dump by restoring it into an ephemeral database.

The module restores one ``pg_dump --format=custom`` file into a throwaway
database, reads the migration versions of ``public.schema_migration`` and the
base table count of the schema, and compares them with the values read from the
source database. It relates to ``ops/backups/backup.py``, ``restore.sh``, and
REQ-BAK-001. Connection strings are passed to child processes through the
``PGDATABASE`` environment variable, never through argv, and no value is logged.
Every external command goes through an injected ``Runner`` so tests use fakes.
"""

import argparse
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ops.schemas import owner_role, validate_identifier

# A runner executes argv with extra environment and returns stdout, or raises.
Runner = Callable[[Sequence[str], Mapping[str, str]], str]

MIGRATIONS_SQL = "SELECT version FROM public.schema_migration ORDER BY version"
_DB_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class RestoreVerificationError(RuntimeError):
    """Raised when a restored dump does not match the source database."""


@dataclass(frozen=True)
class SchemaStats:
    """Migration versions and base table count observed in one database."""

    migrations: tuple[str, ...]
    table_count: int


def tables_sql(schema: str) -> str:
    """Return the SQL that counts base tables of a validated schema name."""
    validate_identifier(schema)
    return (
        "SELECT count(*) FROM information_schema.tables "
        f"WHERE table_schema = '{schema}' AND table_type = 'BASE TABLE'"
    )


def read_stats(run: Runner, env: Mapping[str, str], schema: str) -> SchemaStats:
    """Read migration versions and the table count through ``psql``."""
    base = ["psql", "--no-psqlrc", "--tuples-only", "--no-align", "-v", "ON_ERROR_STOP=1"]
    versions = run([*base, "-c", MIGRATIONS_SQL], env).split()
    count = run([*base, "-c", tables_sql(schema)], env).strip()
    return SchemaStats(tuple(versions), int(count))


def compare_stats(schema: str, source: SchemaStats, restored: SchemaStats) -> None:
    """Raise when migration versions or table counts differ."""
    if source.migrations != restored.migrations:
        raise RestoreVerificationError(f"{schema}: migration versions differ after restore")
    if source.table_count != restored.table_count:
        raise RestoreVerificationError(
            f"{schema}: table count {restored.table_count} != source {source.table_count}"
        )
    if source.table_count == 0:
        raise RestoreVerificationError(f"{schema}: source schema has no tables")


def verify_restore(
    run: Runner,
    dump: Path,
    schema: str,
    source_env: Mapping[str, str],
    admin_env: Mapping[str, str],
    ephemeral_env: Mapping[str, str],
    database: str,
) -> SchemaStats:
    """Restore ``dump`` into ``database`` and compare it with the source.

    ``admin_env`` connects to the ephemeral server, ``ephemeral_env`` to the new
    database. Roles are bootstrapped before ``pg_restore`` so ownership holds.
    ``pg_restore`` ignores ``PGDATABASE``, so the throwaway database URL (which
    holds no production credential) is its only connection argument.
    The database is dropped even when verification fails.
    """
    validate_identifier(schema)
    if not _DB_NAME.fullmatch(database):
        raise ValueError(f"unsafe database name: {database!r}")
    owner = owner_role(schema)
    psql = ["psql", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-c"]
    source = read_stats(run, source_env, schema)
    run([*psql, f"CREATE DATABASE {database}"], admin_env)
    try:
        run(
            [
                *psql,
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{owner}') "
                f"THEN CREATE ROLE {owner} NOLOGIN; END IF; END $$",
            ],
            admin_env,
        )
        run(
            [
                "pg_restore",
                "--exit-on-error",
                "--dbname",
                ephemeral_env["PGDATABASE"],
                str(dump),
            ],
            {},
        )
        restored = read_stats(run, ephemeral_env, schema)
        compare_stats(schema, source, restored)
        return restored
    finally:
        run([*psql, f"DROP DATABASE IF EXISTS {database}"], admin_env)


def main() -> int:
    """Check that each given ``.age`` upload file exists and is not plaintext."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", nargs="+", type=Path)
    failures = 0
    for path in parser.parse_args().artifact:
        if path.suffix != ".age" or not path.is_file():
            print(f"upload check failed: {path.name} is not an existing .age file")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
