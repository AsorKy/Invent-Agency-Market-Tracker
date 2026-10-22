"""Bootstrap must work for a non-superuser connecting role (REQ-DAT-005, T-132)."""

import sys
import types
from typing import Any

import pytest

from ops import bootstrap_roles
from ops.schemas import SCHEMAS, owner_role


def test_each_owner_role_is_granted_to_current_user() -> None:
    sql = bootstrap_roles.generate_sql()
    for s in SCHEMAS:
        o = owner_role(s)
        assert f"GRANT {o} TO CURRENT_USER WITH SET TRUE" in sql
        assert f"GRANT {o} TO CURRENT_USER'" in sql
        assert sql.index(f"CREATE ROLE {o} ") < sql.index(f"GRANT {o} TO CURRENT_USER")
        assert sql.index(f"GRANT {o} TO CURRENT_USER") < sql.index(f"ALTER SCHEMA {s} OWNER")


def test_grant_is_version_gated_and_skipped_for_superuser() -> None:
    sql = bootstrap_roles.generate_sql(["core"])
    assert "server_version_num')::int >= 160000" in sql
    assert "rolsuper FROM pg_roles WHERE rolname = current_user" in sql


class _Err(Exception):
    sqlstate: str | None = None

    def __init__(self, msg: str = "", sqlstate: str | None = None) -> None:
        super().__init__(msg)
        self.sqlstate = sqlstate


class _Conn:
    def __init__(self, log: list[str], sqlstate: str | None = "42501") -> None:
        self.log = log
        self.sqlstate = sqlstate

    def __enter__(self) -> "_Conn":
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def execute(self, sql: str) -> None:
        self.log.append(sql)
        if "pg_cron" in sql and sql.startswith("CREATE EXTENSION"):
            raise _Err("not allowed", self.sqlstate)


def test_unavailable_extension_is_reported_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[str] = []
    fake = types.SimpleNamespace(connect=lambda *a, **k: _Conn(log), Error=_Err)
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    missing = bootstrap_roles.execute_tolerant("postgresql://x", {})
    assert missing == ["pg_cron"]
    assert "CREATE EXTENSION IF NOT EXISTS vector" in log[0]
    assert "CREATE EXTENSION" not in log[-1]
    assert "ALTER SCHEMA" in log[-1]


@pytest.mark.parametrize("sqlstate", ["42501", "58P01"])
def test_tolerated_sqlstates_are_reported(monkeypatch: pytest.MonkeyPatch, sqlstate: str) -> None:
    log: list[str] = []
    fake = types.SimpleNamespace(connect=lambda *a, **k: _Conn(log, sqlstate), Error=_Err)
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    assert bootstrap_roles.execute_tolerant("postgresql://x", {}) == ["pg_cron"]


def test_other_database_errors_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[str] = []
    fake = types.SimpleNamespace(connect=lambda *a, **k: _Conn(log, "08006"), Error=_Err)
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    with pytest.raises(_Err):
        bootstrap_roles.execute_tolerant("postgresql://x", {})
    assert not any("ALTER SCHEMA" in q for q in log)
