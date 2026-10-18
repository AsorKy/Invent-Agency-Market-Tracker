"""Offline SQL consistency checks (decision D-18, REQ-QA-002, REQ-DAT-001, REQ-DAT-005).

Every SQL string handed to ``text(...)`` or ``exec_driver_sql(...)`` in
``services/`` and ``libs/`` is parsed with ``pglast`` and checked against the
schema that ``migrations/`` builds: tables, columns, INSERT column lists and
ON CONFLICT targets. Nothing needs a database. Whatever the checks cannot judge
is listed below with a reason; the tests fail when a list entry goes stale, so
the lists cannot hide new problems. ``tests/integration/test_sql_postgres.py``
(T-095) is the database tier of the same decision.
"""

from collections import Counter

import pytest
from support_sql import (
    Problem,
    Statement,
    analyze,
    collect_statements,
    load_schema,
    migration_files,
    replace_binds,
    schema_from_sql,
)

# SQL built with f-strings. Key: (file, text that occurs in exactly one such
# statement). The statement is still analyzed with every ``{...}`` removed, so
# the fixed skeleton is checked; only the interpolated fragment is not.
DYNAMIC_SQL: dict[tuple[str, str], str] = {
    ("services/core/app/repository.py", "FROM core.company\n"): (
        "list_companies: {cursor_filter} is either empty or one fixed keyset "
        "predicate on updated_at and id built in the same function."
    ),
    ("services/core/app/repository.py", "JOIN core.company c ON c.id = l.company_id"): (
        "list_leads: {where} joins fixed filter predicates from a literal dict in "
        "the same function; every value is bound, none is interpolated."
    ),
    ("services/core/app/repository.py", "FROM core.project\n"): (
        "list_projects: {where} joins fixed predicates (status, tenant_id, keyset) "
        "built in the same function; every value is bound."
    ),
}

# Names passed to text(...) that no static rule resolves to a string. Key:
# (file, variable name). Empty: every variable is resolved or checked by fallback.
UNRESOLVED_SQL: dict[tuple[str, str], str] = {
    ("services/ingest/app/to_core.py", "statement"): (
        "flush() runs statements buffered as (sql, params) tuples whose SQL is a literal "
        "inside the same class; the collector does not follow tuples yet, so those "
        "statements are not analyzed. Follow-up: teach the collector tuple literals."
    ),
}

# Constructs the analyzer cannot judge. Key: (file, problem message).
UNSUPPORTED: dict[tuple[str, str], str] = {}

# Genuine mismatches between services code and the schema, kept so they stay
# visible instead of failing the build. Key: (file, problem kind, message).
# Each needs an owner fix; remove the entry when the fix lands.
KNOWN_FINDINGS: dict[tuple[str, str, str], str] = {}

# Statements and migration constructs that the loader leaves out on purpose.
# ``load_schema`` records anything else in ``Schema.ignored``.
ALLOWED_IGNORED_MIGRATION_ITEMS: dict[str, str] = {}

CHECK_KINDS = ("parse", "table", "column", "insert-column", "conflict")


@pytest.fixture(scope="module")
def schema():
    return load_schema()


@pytest.fixture(scope="module")
def statements() -> list[Statement]:
    return collect_statements()


@pytest.fixture(scope="module")
def findings(schema, statements) -> list[tuple[Statement, Problem]]:
    found: list[tuple[Statement, Problem]] = []
    for statement in statements:
        if statement.sql is not None:
            found.extend((statement, problem) for problem in analyze(statement.sql, schema))
    return found


def _is_known(statement: Statement, problem: Problem) -> bool:
    return (statement.file, problem.kind, problem.message) in KNOWN_FINDINGS


# --------------------------------------------------------------------------- #
# Collection and schema
# --------------------------------------------------------------------------- #


def test_collects_statements_from_the_named_files(statements):
    files = {statement.file for statement in statements}
    assert "services/core/app/repository.py" in files
    assert "services/ingest/app/budget.py" in files
    assert "services/pulse/app/rollups.py" in files
    assert len(statements) >= 50


def test_resolves_module_constants_and_implicit_concatenation(statements):
    rollups = [s for s in statements if s.file == "services/pulse/app/rollups.py"]
    assert {s.kind for s in rollups} == {"name"}
    assert any("pulse.fact_signal" in (s.sql or "") for s in rollups)
    joined = [s for s in statements if "AND aggregate_id = :id" in (s.sql or "")]
    assert joined, "implicit string concatenation must be joined into one statement"


def test_unresolved_names_are_listed(statements):
    unresolved = {(s.file, s.detail) for s in statements if s.kind == "unresolved"}
    assert unresolved == set(UNRESOLVED_SQL), f"unresolved SQL variables: {sorted(unresolved)}"
    assert all(reason.strip() for reason in UNRESOLVED_SQL.values())


def test_dynamic_sql_is_listed_with_a_reason_line_by_line(statements):
    dynamic = [s for s in statements if s.kind == "fstring"]
    used: Counter[tuple[str, str]] = Counter()
    unlisted = []
    for statement in dynamic:
        keys = [
            key
            for key in DYNAMIC_SQL
            if key[0] == statement.file and key[1] in (statement.sql or "")
        ]
        used.update(keys)
        if len(keys) != 1:
            unlisted.append(statement.where)
    assert not unlisted, f"f-string SQL not listed exactly once in DYNAMIC_SQL: {unlisted}"
    stale = [key for key in DYNAMIC_SQL if used[key] != 1]
    assert not stale, f"DYNAMIC_SQL entries that match no or several statements: {stale}"
    assert all(reason.strip() for reason in DYNAMIC_SQL.values())


def test_schema_replays_every_migration_in_order(schema):
    names = migration_files()
    assert [n for n in names if "contract_" in n] == names[-1:], "contract_ files go last"
    assert names[-1].startswith("migrations/contract_")
    assert schema.files == names
    assert "core.company" in schema.tables
    assert "socioeconomic.source_dataset" not in schema.tables, "contract drops the legacy schema"
    assert "socio.source_dataset" in schema.tables


def test_schema_loader_ignores_only_listed_constructs(schema):
    assert schema.ignored == set(ALLOWED_IGNORED_MIGRATION_ITEMS), sorted(schema.ignored)


def test_schema_records_keys_from_every_kind_of_definition(schema):
    keys = schema.tables["ingest.failed_fetch"].keys
    partial = [k for k in keys if k.predicate is not None]
    assert any(
        k.columns == {"source", "coalesce(external_id, '')"}
        and k.predicate == "dead_lettered_at is null"
        for k in partial
    ), "partial unique index with an expression from migration 0005"
    assert any(k.kind == "primary" for k in schema.tables["pulse.fact_signal"].keys)
    assert any(
        k.columns == {"tenant_id", "nit"} and k.predicate == "nit is not null"
        for k in schema.tables["core.company"].keys
    )


# --------------------------------------------------------------------------- #
# The checks on the real statements
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", CHECK_KINDS)
def test_statements_are_consistent_with_the_schema(findings, kind):
    bad = [
        f"{statement.where}: {problem.message}"
        for statement, problem in findings
        if problem.kind == kind and not _is_known(statement, problem)
    ]
    assert not bad, f"{kind} problems:\n" + "\n".join(bad)


def test_unsupported_constructs_are_allow_listed_with_a_reason(findings):
    seen = {
        (statement.file, problem.message)
        for statement, problem in findings
        if problem.kind == "unsupported"
    }
    assert seen == set(UNSUPPORTED), f"unsupported constructs: {sorted(seen)}"
    assert all(reason.strip() for reason in UNSUPPORTED.values())


def test_known_findings_are_still_real(findings):
    live = {(s.file, p.kind, p.message) for s, p in findings}
    stale = [key for key in KNOWN_FINDINGS if key not in live]
    assert not stale, f"KNOWN_FINDINGS entries that no longer occur: {stale}"
    assert all(reason.strip() for reason in KNOWN_FINDINGS.values())


def test_every_statement_was_analyzed(statements):
    assert [
        s.where for s in statements if s.sql is None and (s.file, s.detail) not in UNRESOLVED_SQL
    ] == []


def test_on_conflict_statements_exist_and_are_checked(statements):
    conflicts = [s for s in statements if "on conflict" in (s.sql or "").lower()]
    assert len(conflicts) >= 5


# --------------------------------------------------------------------------- #
# The analyzer itself, against a synthetic schema
# --------------------------------------------------------------------------- #

SYNTHETIC = schema_from_sql(
    [
        """
        CREATE SCHEMA s;
        CREATE TABLE s.item (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            name text,
            closed_at timestamptz,
            UNIQUE (tenant_id, name)
        );
        ALTER TABLE s.item ADD COLUMN IF NOT EXISTS note text;
        CREATE UNIQUE INDEX ux_open ON s.item (tenant_id, COALESCE(note, ''))
            WHERE closed_at IS NULL;
        CREATE TABLE s.other (item_id uuid, qty int);
        """
    ]
)


def kinds(sql: str) -> list[str]:
    return [problem.kind for problem in analyze(sql, SYNTHETIC)]


def test_analyzer_accepts_aliases_ctes_subqueries_and_qualified_names():
    assert (
        kinds(
            """
        WITH recent AS (SELECT i.id, i.name AS label FROM s.item AS i WHERE i.tenant_id = :t)
        SELECT r.label, o.qty, (SELECT max(qty) FROM s.other x WHERE x.item_id = r.id) AS top
        FROM recent r
        JOIN (SELECT item_id, sum(qty) AS qty FROM s.other GROUP BY item_id) o ON o.item_id = r.id
        ORDER BY top DESC, r.label
        """
        )
        == []
    )


def test_analyzer_reports_unknown_table_and_column():
    assert kinds("SELECT 1 FROM s.missing") == ["table"]
    assert kinds("SELECT nope FROM s.item") == ["column"]
    assert kinds("SELECT i.nope FROM s.item i") == ["column"]
    assert kinds("SELECT z.id FROM s.item i") == ["table"]
    assert kinds("SELECT label FROM (SELECT name AS other FROM s.item) q") == ["column"]
    assert kinds("WITH c AS (SELECT id FROM s.item) SELECT name FROM c") == ["column"]
    assert kinds("DELETE FROM s.item WHERE nope = 1") == ["column"]
    assert kinds("UPDATE s.item SET nope = 1") == ["column"]
    assert (
        kinds("UPDATE s.item i SET name = o.qty::text FROM s.other o WHERE o.item_id = i.id") == []
    )


def test_analyzer_reports_parse_errors_and_replaces_binds():
    assert kinds("SELEC 1") == ["parse"]
    assert (
        replace_binds("SELECT :a::int, ':b', :a, :c -- :d") == "SELECT $1::int, ':b', $1, $2 -- :d"
    )
    assert kinds("SELECT id FROM s.item WHERE tenant_id = :t AND name = ANY(:names)") == []


def test_analyzer_checks_insert_column_lists():
    assert kinds("INSERT INTO s.item (id, name) VALUES (:id, :name)") == []
    assert kinds("INSERT INTO s.item (id, bogus) VALUES (:id, :b)") == ["insert-column"]
    assert kinds("INSERT INTO s.nowhere (id) VALUES (:id)") == ["table"]


def test_analyzer_checks_on_conflict_targets():
    insert = "INSERT INTO s.item (id, tenant_id, name) VALUES (:i, :t, :n) "
    assert kinds(insert + "ON CONFLICT (id) DO NOTHING") == []
    assert kinds(insert + "ON CONFLICT (name, tenant_id) DO UPDATE SET name = excluded.name") == []
    assert kinds(insert + "ON CONFLICT (name) DO NOTHING") == ["conflict"]
    assert kinds(insert + "ON CONFLICT DO NOTHING") == []
    assert kinds(insert + "ON CONFLICT DO UPDATE SET name = 'x'") != []
    assert kinds(insert + "ON CONFLICT (id) DO UPDATE SET bogus = excluded.name") == ["column"]
    assert kinds(insert + "ON CONFLICT (id) DO UPDATE SET name = excluded.bogus") == ["column"]


def test_partial_index_matches_only_when_the_predicate_is_repeated():
    insert = "INSERT INTO s.item (id, tenant_id, note) VALUES (:i, :t, :n) "
    target = "ON CONFLICT (tenant_id, COALESCE(note, '')) "
    assert kinds(insert + target + "DO NOTHING") == ["conflict"]
    assert kinds(insert + target + "WHERE closed_at IS NULL DO NOTHING") == []
    assert kinds(insert + target + "WHERE item.closed_at IS NULL DO NOTHING") == []
    assert kinds(insert + target + "WHERE closed_at IS NOT NULL DO NOTHING") == ["conflict"]


def test_cte_insert_with_returning_feeds_the_next_statement():
    assert (
        kinds(
            """
        WITH ins AS (INSERT INTO s.item (id, tenant_id) VALUES (:i, :t) RETURNING id)
        INSERT INTO s.other (item_id, qty) SELECT id, 1 FROM ins
        """
        )
        == []
    )
    assert kinds(
        """
        WITH ins AS (INSERT INTO s.item (id, tenant_id) VALUES (:i, :t) RETURNING id)
        SELECT bogus FROM ins
        """
    ) == ["column"]


def test_drop_schema_removes_its_tables():
    schema = schema_from_sql(
        [
            "CREATE SCHEMA a; CREATE TABLE a.t (x int); CREATE TABLE b.u (y int);",
            "DROP SCHEMA a CASCADE;",
        ]
    )
    assert set(schema.tables) == {"b.u"}
