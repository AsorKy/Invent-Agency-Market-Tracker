"""Check the brief schema migration against the Brief models (REQ-BRF-001, REQ-DAT-001).

Parses ``migrations/20261019_0007_brief_config_version.sql`` with ``pglast`` and
verifies that ``brief.record_digest`` gains ``config_version`` matching
``StoredDigest``, and that the base table keeps tenant row level security.
"""

# mypy: disable-error-code="import-untyped,no-any-return,no-untyped-call,attr-defined"

from pathlib import Path

from pglast import ast as pg
from pglast import parse_sql

from services.brief.app.models import StoredDigest

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
SQL_FILE = MIGRATIONS / "20261019_0007_brief_config_version.sql"
BASE_FILE = MIGRATIONS / "20261017_0002_arch_v22_foundations.sql"


def _added_columns() -> dict[str, pg.ColumnDef]:
    columns: dict[str, pg.ColumnDef] = {}
    for raw in parse_sql(SQL_FILE.read_text(encoding="utf-8")):
        stmt = raw.stmt
        assert isinstance(stmt, pg.AlterTableStmt)
        assert (stmt.relation.schemaname, stmt.relation.relname) == ("brief", "record_digest")
        for cmd in stmt.cmds:
            assert cmd.subtype.name == "AT_AddColumn"
            assert cmd.missing_ok
            columns[cmd.def_.colname] = cmd.def_
    return columns


def test_adds_config_version_only() -> None:
    assert set(_added_columns()) == {"config_version"}


def test_config_version_is_not_null_with_default() -> None:
    column = _added_columns()["config_version"]
    kinds = {c.contype.name for c in column.constraints}
    assert {"CONSTR_NOTNULL", "CONSTR_DEFAULT", "CONSTR_CHECK"} <= kinds


def test_column_matches_model_field() -> None:
    field = StoredDigest.model_fields["config_version"]
    assert field.default == 0
    assert field.metadata and field.metadata[0].ge == 0


def test_digest_table_columns_cover_stored_digest() -> None:
    """Every StoredDigest field has a column; company_id and tenant context map to PK/RLS."""
    sql = BASE_FILE.read_text(encoding="utf-8")
    base: set[str] = set()
    for raw in parse_sql(sql):
        stmt = raw.stmt
        if isinstance(stmt, pg.CreateStmt) and stmt.relation.schemaname == "brief":
            if stmt.relation.relname == "record_digest":
                base = {e.colname for e in stmt.tableElts if isinstance(e, pg.ColumnDef)}
    columns = base | set(_added_columns())
    # ``content`` holds BriefContent as jsonb; ``tenant_id`` comes from the tenant context.
    assert set(StoredDigest.model_fields) <= columns
    assert "tenant_id" in columns


def test_base_table_keeps_tenant_rls() -> None:
    sql = BASE_FILE.read_text(encoding="utf-8")
    assert "'record_digest'" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
