"""Assert the schema and role catalog of the migrations and the roles example.

Relates to REQ-DAT-001 and SPEC section 3.2: 7 schemas and 14 roles. The test
parses ``migrations/*.sql`` and ``ops/bootstrap_roles.sql.example`` as text.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))
ROLES_EXAMPLE = ROOT / "ops" / "bootstrap_roles.sql.example"

SCHEMAS = {"identity", "ingest", "core", "pulse", "brief", "ml", "socio"}
ROLES = {f"{s}_{kind}" for s in SCHEMAS for kind in ("owner", "app")}
CREATE_SCHEMA = re.compile(r"^\s*CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.I | re.M)
DROP_SCHEMA = re.compile(r"^\s*DROP\s+SCHEMA\s+(?:IF\s+EXISTS\s+)?(\w+)", re.I | re.M)
CREATE_ROLE = re.compile(r"CREATE\s+ROLE\s+(\w+)", re.I)


def _net_schemas() -> set[str]:
    created: set[str] = set()
    for path in MIGRATIONS:
        sql = path.read_text(encoding="utf-8")
        created |= set(CREATE_SCHEMA.findall(sql))
        if path.name.startswith("contract_"):
            created -= set(DROP_SCHEMA.findall(sql))
    return created


def test_roles_example_has_exactly_the_catalog_roles() -> None:
    text = ROLES_EXAMPLE.read_text(encoding="utf-8")
    roles = CREATE_ROLE.findall(text)
    assert len(roles) == len(set(roles)) == 14
    assert set(roles) == ROLES


def test_socioeconomic_is_dropped_only_by_a_contract_migration() -> None:
    droppers = [
        p.name
        for p in MIGRATIONS
        if "socioeconomic" in DROP_SCHEMA.findall(p.read_text(encoding="utf-8"))
    ]
    assert droppers
    assert all(name.startswith("contract_") for name in droppers)
    assert "socioeconomic" not in _net_schemas()


def test_canonical_schemas_are_all_created() -> None:
    assert SCHEMAS <= _net_schemas()


def test_no_unexpected_schema_besides_known_legacy() -> None:
    assert _net_schemas() <= SCHEMAS | {"validation"}


def test_analytics_is_dropped_only_by_a_contract_migration() -> None:
    droppers = [
        p.name
        for p in MIGRATIONS
        if "analytics" in DROP_SCHEMA.findall(p.read_text(encoding="utf-8"))
    ]
    assert droppers
    assert all(name.startswith("contract_") for name in droppers)
    assert "analytics" not in _net_schemas()


def test_analytics_contract_copies_into_pulse_before_the_drop() -> None:
    sql = next(p for p in MIGRATIONS if "retire_analytics" in p.name).read_text(encoding="utf-8")
    for table in ("fact_signal", "fact_company_snapshot", "fact_lead_outcome"):
        assert f"INSERT INTO pulse.{table}" in sql
        assert f"FROM analytics.{table}" in sql
    assert sql.index("INSERT INTO pulse") < sql.index("DROP SCHEMA IF EXISTS analytics")


def test_roles_example_has_no_legacy_names() -> None:
    text = ROLES_EXAMPLE.read_text(encoding="utf-8")
    assert "analytics" not in text
    assert "socioeconomic" not in text


CATALOG: dict[str, set[str]] = {
    "identity": {"tenant", "user_account", "tenant_membership", "membership_scope"},
    "ingest": {
        "raw_snapshot",
        "failed_fetch",
        "budget",
        "source_registry",
        "entity_candidate",
        "entity_match_log",
        "quarantine",
        "rues_lookup_queue",
        "processed_event",
        "pending_snapshot",
        "failed_record",
    },
    "core": {
        "company",
        "location",
        "person_role",
        "signal",
        "lead",
        "lead_outcome",
        "lead_activity",
        "pipeline_stage",
        "project",
        "tenant_config",
        "tenant_config_history",
        "lead_score_rule",
        "upload_blob",
        "job",
        "outbox_event",
        "outbox_dead_letter",
        "audit_log",
        "processed_event",
    },
    "pulse": {
        "fact_signal",
        "fact_company_snapshot",
        "fact_lead_outcome",
        "daily_rollup",
        "semantic_metric_definition",
        "processed_event",
    },
    "brief": {"record_digest", "single_flight_lock", "processed_event"},
    "ml": {
        "feature_store",
        "company_segment",
        "lead_score",
        "model_version",
        "score_history",
        "single_flight_lock",
        "processed_event",
    },
    "socio": {"source_dataset", "indicator_raw", "indicator_normalized", "composite_index"},
}
CONSUMERS = ("ingest", "core", "pulse", "brief", "ml")
CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\.(\w+)\s*\((.*?)\n\);", re.I | re.S
)
ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\.(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+([\w()]+)",
    re.I,
)
RLS = re.compile(r"ALTER\s+TABLE\s+(\w+)\.(\w+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.I)


def _all_sql() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)


def _tables() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for schema, table, _ in CREATE_TABLE.findall(_all_sql()):
        found.setdefault(schema, set()).add(table)
    return found


def _columns(schema: str, table: str) -> set[str]:
    sql = _all_sql()
    cols: set[str] = set()
    for s, t, body in CREATE_TABLE.findall(sql):
        if (s, t) == (schema, table):
            for line in body.splitlines():
                word = line.strip().split(" ")[0]
                if (
                    word
                    and word.islower()
                    and word not in {"unique", "primary", "check", "foreign"}
                ):
                    cols.add(word)
    cols |= {c for s, t, c, _ in ADD_COLUMN.findall(sql) if (s, t) == (schema, table)}
    return cols


def test_every_catalog_table_exists_in_its_owning_schema() -> None:
    tables = _tables()
    for schema, expected in CATALOG.items():
        assert expected <= tables.get(schema, set()), (schema, expected - tables.get(schema, set()))


def test_analytics_tables_live_in_pulse_and_brief_tables_in_brief() -> None:
    tables = _tables()
    assert {"fact_company_snapshot", "fact_lead_outcome"} <= tables["pulse"]
    assert "record_digest" in tables["brief"]


def test_each_consumer_schema_has_processed_event() -> None:
    tables = _tables()
    for schema in CONSUMERS:
        assert "processed_event" in tables[schema], schema


def test_lead_and_activity_columns() -> None:
    assert {"source", "city", "service_line", "holdout"} <= _columns("core", "lead")
    assert {"channel", "source"} <= _columns("core", "lead_activity")


def test_holdout_is_nullable() -> None:
    match = re.search(r"core\.lead\s+ADD COLUMN IF NOT EXISTS holdout ([^;]*);", _all_sql())
    assert match
    assert "NOT NULL" not in match.group(1).upper()


def test_tenant_config_has_etag_version() -> None:
    assert "version" in _columns("core", "tenant_config")


def test_rls_enabled_on_company_lead_project() -> None:
    enabled = {(s, t) for s, t in RLS.findall(_all_sql())}
    assert {("core", "company"), ("core", "lead"), ("core", "project")} <= enabled
    assert "company_tenant_policy" in _all_sql()


def test_no_json_float_or_real_columns() -> None:
    bad = re.compile(r"^\s+\w+\s+(json|float\w*|real|double)\b", re.I | re.M)
    assert not bad.findall(_all_sql())


VALIDATION_TABLES = {
    "financial_run",
    "unit_economics",
    "funnel_metric",
    "growth_metric",
    "portfolio_snapshot",
    "meta_ml_metric",
}


def test_validation_schema_has_the_six_tables_and_is_operated_by_core() -> None:
    from ops.schemas import CATALOG_SCHEMAS, app_role, owner_role
    from ops.schemas import SCHEMAS as SINGLE_LIST

    assert _tables()["validation"] == VALIDATION_TABLES
    assert "validation" in CATALOG_SCHEMAS
    assert "validation" not in SINGLE_LIST, "validation adds no roles"
    assert (owner_role("validation"), app_role("validation")) == ("core_owner", "core_app")
    assert not any("validation_" in role for role in ROLES)


@pytest.mark.parametrize("table", sorted(VALIDATION_TABLES))
def test_validation_tables_carry_the_required_columns_and_rls(table: str) -> None:
    assert {"source_version", "project_id", "measured_at", "tenant_id"} <= _columns(
        "validation", table
    )
    sql = _all_sql()
    assert f"'{table}'" in sql  # listed in the RLS loop of the migration
