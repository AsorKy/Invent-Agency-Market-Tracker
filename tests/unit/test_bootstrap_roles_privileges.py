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
    pass


class _Conn:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def __enter__(self) -> "_Conn":
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def execute(self, sql: str) -> None:
        self.log.append(sql)
        if "pg_cron" in sql and sql.startswith("CREATE EXTENSION"):
            raise _Err("not allowed")


def test_unavailable_extension_is_reported_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[str] = []
    fake = types.SimpleNamespace(connect=lambda *a, **k: _Conn(log), Error=_Err)
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    missing = bootstrap_roles.execute_tolerant("postgresql://x", {})
    assert missing == ["pg_cron"]
    assert "CREATE EXTENSION IF NOT EXISTS vector" in log[0]
    assert "CREATE EXTENSION" not in log[-1]
    assert "ALTER SCHEMA" in log[-1]
