"""Tests for the migration runner using a fake connection (REQ-RUN-002)."""

import asyncio
from pathlib import Path

import pytest

from ops import migrate


class FakeConn:
    def __init__(self):
        self.table = {}
        self.executed = []
        self.in_tx = False
        self.fail_on = None
        self.events = []

    def transaction(self):
        conn = self

        class Tx:
            async def __aenter__(self):
                conn.in_tx = True
                conn.snapshot = dict(conn.table)
                conn.events.append("begin")

            async def __aexit__(self, exc_type, exc, tb):
                conn.in_tx = False
                conn.events.append("rollback" if exc_type else "commit")
                if exc_type:
                    conn.table = conn.snapshot
                return False

        return Tx()

    async def execute(self, sql, *args):
        if "CREATE TABLE IF NOT EXISTS public.schema_migration" in sql:
            return
        if sql.startswith("INSERT INTO public.schema_migration"):
            assert self.in_tx
            self.table[args[0]] = args[1]
            return
        assert self.in_tx
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom")
        self.executed.append(sql)

    async def fetch(self, sql, *args):
        return [{"version": v, "checksum": c} for v, c in self.table.items()]


def make_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    return tmp_path


def test_real_order_places_contract_file_by_version():
    names = [p.name for p in migrate.list_migrations()]
    assert (
        names.index("contract_20261018_0003_retire_socioeconomic.sql")
        == names.index("20261017_0002_arch_v22_foundations.sql") + 1
    )
    assert (
        names.index("20261018_0004_run1_schema_additions.sql")
        == names.index("contract_20261018_0003_retire_socioeconomic.sql") + 1
    )


def test_applies_in_order_with_one_transaction_each(tmp_path):
    d = make_dir(tmp_path, {"b.sql": "SELECT 2", "a.sql": "SELECT 1"})
    conn = FakeConn()
    done = asyncio.run(migrate.migrate(conn, d))
    assert done == ["a.sql", "b.sql"]
    assert conn.executed == ["SELECT 1", "SELECT 2"]
    assert conn.events == ["begin", "commit", "begin", "commit"]
    assert conn.table["a.sql"] == migrate.checksum(d / "a.sql")


def test_rerun_is_idempotent(tmp_path):
    d = make_dir(tmp_path, {"a.sql": "SELECT 1"})
    conn = FakeConn()
    asyncio.run(migrate.migrate(conn, d))
    assert asyncio.run(migrate.migrate(conn, d)) == []
    assert conn.executed == ["SELECT 1"]


def test_changed_checksum_is_refused(tmp_path):
    d = make_dir(tmp_path, {"a.sql": "SELECT 1"})
    conn = FakeConn()
    asyncio.run(migrate.migrate(conn, d))
    (d / "a.sql").write_text("SELECT 99")
    with pytest.raises(migrate.ChecksumError):
        asyncio.run(migrate.migrate(conn, d))
    assert conn.executed == ["SELECT 1"]


def test_failure_rolls_back_and_records_nothing(tmp_path):
    d = make_dir(tmp_path, {"a.sql": "SELECT 1", "b.sql": "BAD"})
    conn = FakeConn()
    conn.fail_on = "BAD"
    with pytest.raises(RuntimeError):
        asyncio.run(migrate.migrate(conn, d))
    assert list(conn.table) == ["a.sql"]
    assert conn.events[-1] == "rollback"


def test_dry_run_writes_nothing(tmp_path):
    d = make_dir(tmp_path, {"a.sql": "SELECT 1"})
    conn = FakeConn()
    assert asyncio.run(migrate.migrate(conn, d, dry_run=True)) == ["a.sql"]
    assert conn.executed == [] and conn.table == {}


def test_cli_dry_run_without_dsn_lists_files(capsys):
    assert migrate.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would apply 20261017_0001_expand_initial_schemas.sql" in out
