"""Parity between identity SQL migration columns and the Django models (REQ-IDN-001).

Reads ``migrations/20261019_0006_identity_session_tables.sql`` with ``pglast`` and
compares each column (name, nullability, primary key) with the fields of the
``Credential`` and ``AuthSession`` models under the SQLite test settings.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call,attr-defined"

import os
from pathlib import Path

import pytest

os.environ["DJANGO_SETTINGS_MODULE"] = "services.identity.project.settings_test"

import django  # noqa: E402

django.setup()

from pglast import ast as pg  # noqa: E402
from pglast import parse_sql  # noqa: E402

from services.identity.iam.models import AuthSession, Credential  # noqa: E402

SQL_FILE = (
    Path(__file__).resolve().parents[2] / "migrations" / "20261019_0006_identity_session_tables.sql"
)
MODELS = {"credential": Credential, "auth_session": AuthSession}


def _sql_columns() -> dict[str, dict[str, tuple[bool, bool]]]:
    """Return table -> column -> (not_null, primary_key) from the CREATE TABLE statements."""
    tables: dict[str, dict[str, tuple[bool, bool]]] = {}
    for raw in parse_sql(SQL_FILE.read_text(encoding="utf-8")):
        stmt = raw.stmt
        if not isinstance(stmt, pg.CreateStmt):
            continue
        assert stmt.relation.schemaname == "identity"
        columns: dict[str, tuple[bool, bool]] = {}
        for element in stmt.tableElts:
            kinds = {c.contype.name for c in element.constraints or ()}
            primary = "CONSTR_PRIMARY" in kinds
            columns[element.colname] = (primary or "CONSTR_NOTNULL" in kinds, primary)
        tables[stmt.relation.relname] = columns
    return tables


def test_sql_defines_both_tables() -> None:
    assert set(_sql_columns()) == set(MODELS)


@pytest.mark.parametrize("table", sorted(MODELS))
def test_columns_match_model_fields(table: str) -> None:
    sql = _sql_columns()[table]
    fields = {f.column: f for f in MODELS[table]._meta.concrete_fields}
    assert set(sql) == set(fields)
    for name, (not_null, primary) in sql.items():
        field = fields[name]
        assert not_null == (not field.null), name
        assert primary == field.primary_key, name


def test_model_tables_are_schema_qualified_names() -> None:
    assert Credential._meta.db_table.endswith("credential")
    assert AuthSession._meta.db_table.endswith("auth_session")


def test_auth_session_has_row_level_security_and_credential_does_not() -> None:
    text = SQL_FILE.read_text(encoding="utf-8")
    assert "ALTER TABLE identity.auth_session ENABLE ROW LEVEL SECURITY" in text
    assert "identity.credential ENABLE ROW LEVEL SECURITY" not in text
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in text
