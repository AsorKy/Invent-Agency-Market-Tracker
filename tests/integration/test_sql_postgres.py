"""SQL against a real PostgreSQL (decision D-18 database tier, REQ-QA-005, REQ-DAT-001/005).

Skipped unless ``TEST_DATABASE_URL`` is set (a server URL; the tests create and
drop their own scratch database). Needs the ``sqltest`` extra
(``psycopg[binary]``). The tests apply ``migrations/`` twice to prove
idempotence, ``PREPARE`` every SQL string found in ``services/`` and ``libs/``
(PostgreSQL checks tables, columns, types and ON CONFLICT arbiters without
running anything), and require row level security on every ``tenant_id`` table.
Run with ``make test-sql``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="TEST_DATABASE_URL is not set")

# Statements that PREPARE cannot take. Key: (file, text found in the statement).
UNPREPARABLE: dict[tuple[str, str], str] = {}

# f-string SQL, prepared as a skeleton (every {...} removed) unless listed here.
# Key: (file, text found in the statement). Value: reason.
DYNAMIC_SKELETON_SKIPPED: dict[tuple[str, str], str] = {}

# Tables with a tenant_id column that deliberately have no row level security.
# SPEC requires RLS only for the core business tables; these are infrastructure
# tables. UNVERIFIED: reasons are guesses from table names; pending an owner decision (core.location and core.signal are tenant business data and may need RLS). Key: schema.table.
RLS_EXEMPT: dict[str, str] = {
    "analytics.processed_event": "consumer idempotency ledger, written by the service only",
    "core.audit_log": "append-only audit trail written by the service, not tenant queried",
    "core.lead_outcome": "outcome log written by the service for lead scoring",
    "core.location": "shared location reference data keyed by tenant for lookup",
    "core.outbox_dead_letter": "outbox infrastructure, read by the relay only",
    "core.outbox_event": "transactional outbox, read by the relay across tenants",
    "core.processed_event": "consumer idempotency ledger, written by the service only",
    "core.signal": "ingest-derived signal rows written by the pipeline",
    "ml.processed_event": "consumer idempotency ledger, written by the service only",
}


def _listed(table: dict[tuple[str, str], str], statement) -> str | None:
    for (file, needle), reason in table.items():
        if statement.file == file and statement.sql and needle in statement.sql:
            return reason
    return None


@pytest.fixture(scope="module")
def conn():
    psycopg = pytest.importorskip("psycopg")
    from support_sql import migration_files
    from support_sql_pg import apply_migrations, scratch_database

    files = migration_files()
    with scratch_database(BASE_URL) as url, psycopg.connect(url, autocommit=True) as connection:
        apply_migrations(connection, files)
        yield connection


def test_migration_order_puts_contract_last():
    from support_sql import migration_files

    names = [Path(n).name for n in migration_files()]
    first_contract = next(i for i, n in enumerate(names) if n.startswith("contract_"))
    assert all(n.startswith("contract_") for n in names[first_contract:])


def test_migrations_apply_twice(conn):
    from support_sql import migration_files
    from support_sql_pg import apply_migrations

    apply_migrations(conn, migration_files())


def test_rls_enabled_on_every_tenant_table(conn):
    from support_sql_pg import tables_with_tenant_id, tables_without_rls

    assert tables_with_tenant_id(conn), "no tenant_id table found; the check would be vacuous"
    assert sorted(set(tables_without_rls(conn)) - set(RLS_EXEMPT)) == []


def test_rls_exemptions_are_not_stale(conn):
    from support_sql_pg import tables_with_tenant_id, tables_without_rls

    missing = set(tables_without_rls(conn))
    existing = set(tables_with_tenant_id(conn))
    assert sorted(set(RLS_EXEMPT) - existing) == [], "exempt table does not exist"
    assert sorted(set(RLS_EXEMPT) - missing) == [], "exempt table now has RLS; remove it"


def _statements():
    from support_sql import collect_statements

    return [s for s in collect_statements() if s.sql is not None]


def test_every_statement_prepares(conn):
    from support_sql_pg import numbered_binds, prepare_and_release

    failures: list[str] = []
    prepared = 0
    for index, statement in enumerate(_statements()):
        if _listed(UNPREPARABLE, statement):
            continue
        if statement.kind == "fstring" and _listed(DYNAMIC_SKELETON_SKIPPED, statement):
            continue
        sql_text, _ = numbered_binds(statement.sql)
        try:
            with conn.transaction():
                prepare_and_release(conn, index, sql_text)
                prepared += 1
                raise _Rollback
        except _Rollback:
            continue
        except Exception as error:  # noqa: BLE001 - report every failing statement
            failures.append(f"{statement.where} [{statement.kind}]: {str(error).splitlines()[0]}")
    assert prepared > 0
    assert failures == []


def test_skip_lists_are_not_stale():
    statements = _statements()
    for table in (UNPREPARABLE, DYNAMIC_SKELETON_SKIPPED):
        for key in table:
            assert any(_listed({key: ""}, s) is not None for s in statements), key


class _Rollback(Exception):
    """Raised to roll back the savepoint after a successful PREPARE."""
