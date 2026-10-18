"""Test the migration guardian and the schema-list driven role bootstrap.

Relates to REQ-DAT-006, REQ-DAT-007 and REQ-EXT-003. Pure stdlib: SQL text is
checked in memory and git ranges are built in temporary repositories.
"""

import re
import subprocess
from pathlib import Path

import pytest

from ops import bootstrap_roles, check_migrations
from ops.schemas import EXTENSIONS, SCHEMAS

ROOT = Path(__file__).resolve().parents[2]
ROLES_EXAMPLE = ROOT / "ops" / "bootstrap_roles.sql.example"
CREATE_ROLE = re.compile(r"CREATE\s+ROLE\s+(\w+)", re.I)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE core.company;",
        "ALTER TABLE core.company DROP COLUMN name;",
        "ALTER TABLE core.company RENAME COLUMN a TO b;",
        "ALTER TABLE core.company RENAME TO firm;",
        "ALTER TYPE core.stage ADD VALUE 'x';",
        "ALTER TABLE core.company ALTER COLUMN a TYPE bigint;",
        "ALTER TABLE core.company ALTER COLUMN a SET NOT NULL;",
        "ALTER TABLE core.company ADD COLUMN a text NOT NULL;",
        "alter table core.company add b int not null, add c int;",
    ],
)
def test_destructive_sql_is_rejected_outside_contract(sql: str) -> None:
    assert check_migrations.check_sql("20260101_0001_x.sql", sql)
    assert not check_migrations.check_sql("contract_20260101_0001_x.sql", sql)


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE core.company ADD COLUMN a text;",
        "ALTER TABLE core.company ADD COLUMN a int NOT NULL DEFAULT 0;",
        "ALTER TABLE core.company ADD CONSTRAINT c CHECK (a IS NOT NULL);",
        "CREATE TABLE core.t (id uuid NOT NULL);",
        "-- DROP TABLE core.company;\nSELECT 1;",
    ],
)
def test_expand_sql_is_accepted(sql: str) -> None:
    assert check_migrations.check_sql("20260101_0001_x.sql", sql) == []


def test_unknown_schema_is_rejected_until_added_to_the_list() -> None:
    sql = "CREATE SCHEMA IF NOT EXISTS fake;"
    assert check_migrations.check_sql("20260101_0001_x.sql", sql)
    assert check_migrations.check_sql("20260101_0001_x.sql", sql, (*SCHEMAS, "fake")) == []


def test_fake_schema_is_accepted_by_guardian_and_bootstrap(tmp_path: Path) -> None:
    schemas = (*SCHEMAS, "fake")
    (tmp_path / "20260101_0001_fake.sql").write_text("CREATE SCHEMA IF NOT EXISTS fake;\n")
    assert check_migrations.check_directory(tmp_path, schemas) == []
    sql = bootstrap_roles.generate_sql(schemas)
    assert "CREATE ROLE fake_owner NOLOGIN" in sql
    assert "CREATE ROLE fake_app LOGIN" in sql
    assert "ALTER SCHEMA fake OWNER TO fake_owner" in sql


def test_existing_migrations_pass() -> None:
    assert check_migrations.check_directory() == []


def test_bootstrap_roles_match_example_and_catalog() -> None:
    generated = set(CREATE_ROLE.findall(bootstrap_roles.generate_sql()))
    assert generated == set(CREATE_ROLE.findall(ROLES_EXAMPLE.read_text(encoding="utf-8")))
    assert len(generated) == 14


def test_bootstrap_is_idempotent_and_enables_extensions() -> None:
    sql = bootstrap_roles.generate_sql()
    assert EXTENSIONS == ("vector", "postgis", "pg_cron", "pg_trgm")
    for ext in EXTENSIONS:
        assert f"CREATE EXTENSION IF NOT EXISTS {ext};" in sql
    assert sql.count("CREATE ROLE") == sql.count("IF NOT EXISTS (SELECT FROM pg_roles")


def test_bootstrap_has_no_cross_schema_grants() -> None:
    sql = bootstrap_roles.generate_sql()
    for line in sql.splitlines():
        match = re.search(r"IN SCHEMA (\w+) TO (\w+)_app", line)
        if match:
            assert match.group(1) == match.group(2)
        match = re.search(r"GRANT USAGE ON SCHEMA (\w+) TO (\w+)_app", line)
        if match:
            assert match.group(1) == match.group(2)


def test_bootstrap_rejects_unsafe_identifier_and_quotes_password() -> None:
    with pytest.raises(ValueError):
        bootstrap_roles.generate_sql(["core; DROP"])
    sql = bootstrap_roles.generate_sql(["core"], {"core": "it's"})
    assert "PASSWORD 'it''s'" in sql


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("x\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "tag", "base")
    return tmp_path


def _commit(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")


def test_contract_with_application_code_is_rejected(repo: Path) -> None:
    _commit(
        repo,
        {
            "migrations/contract_20260101_0001_x.sql": "DROP TABLE a;\n",
            "services/core/app.py": "x = 1\n",
        },
    )
    assert check_migrations.check_commit_range("base", repo)


def test_contract_alone_or_with_docs_and_tests_is_accepted(repo: Path) -> None:
    _commit(
        repo,
        {
            "migrations/contract_20260101_0001_x.sql": "DROP TABLE a;\n",
            "docs/why.md": "reason\n",
            "tests/unit/test_x.py": "x = 1\n",
        },
    )
    assert check_migrations.check_commit_range("base", repo) == []


def test_expand_migration_with_code_is_accepted(repo: Path) -> None:
    _commit(
        repo,
        {"migrations/20260101_0001_x.sql": "SELECT 1;\n", "services/core/app.py": "x = 1\n"},
    )
    assert check_migrations.check_commit_range("base", repo) == []
