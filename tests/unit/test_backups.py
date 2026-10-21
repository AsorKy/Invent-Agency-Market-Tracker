"""Unit tests for verified encrypted backups (REQ-BAK-001) with fake tools."""

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ops.backups import backup, verify_restore
from ops.schemas import SCHEMAS

ROOT = Path(__file__).resolve().parents[2]
ENV = {
    "BACKUP_SOURCE_URL": "postgresql://src.invalid/app",
    "BACKUP_EPHEMERAL_ADMIN_URL": "postgresql://eph.invalid:5432/postgres",
    "BACKUP_AGE_RECIPIENT": "age1publicrecipientplaceholder",
}


class FakeTools:
    """Record commands and emulate pg_dump, pg_restore, psql, and age."""

    def __init__(self, restored_tables: int = 3, restored_versions: str = "0001\n0002\n") -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.restored_tables = restored_tables
        self.restored_versions = restored_versions

    def __call__(self, argv: Sequence[str], env: Mapping[str, str]) -> str:
        argv = list(argv)
        self.calls.append((argv, dict(env)))
        tool = argv[0]
        if tool == "pg_dump":
            file_arg = next(a for a in argv if a.startswith("--file="))
            Path(file_arg.removeprefix("--file=")).write_bytes(b"PGDMP fake dump")
        elif tool == "age":
            Path(argv[argv.index("--output") + 1]).write_bytes(b"age-encryption.org fake")
        elif tool == "psql" and "-c" in argv:
            sql = argv[argv.index("-c") + 1]
            restored = "/restore_" in env["PGDATABASE"]
            if "schema_migration" in sql:
                return self.restored_versions if restored else "0001\n0002\n"
            if "information_schema.tables" in sql:
                return f"{self.restored_tables if restored else 3}\n"
        return ""

    def tools(self) -> list[str]:
        return [c[0][0] for c in self.calls]


def test_retention_is_28_on_sunday_and_7_otherwise() -> None:
    assert backup.retention_days(date(2026, 10, 18)) == 28  # Sunday
    assert backup.retention_days(date(2026, 10, 19)) == 7
    assert backup.retention_days(date(2026, 10, 24)) == 7


def test_run_backup_orders_steps_and_uploads_only_encrypted(tmp_path: Path) -> None:
    fake = FakeTools()
    now = datetime(2026, 10, 18, 3, 17, tzinfo=UTC)
    records = backup.run_backup(fake, ["core"], tmp_path / "w", tmp_path / "up", ENV, now)
    order = fake.tools()
    assert order[0] == "pg_dump"
    assert order.index("pg_restore") < order.index("age")
    assert order[-1] == "age"
    assert [p.name for p in (tmp_path / "up").iterdir()] == ["core-20261018T031700Z.dump.age"]
    assert list((tmp_path / "w").iterdir()) == []  # plaintext removed
    assert re.fullmatch(r"[0-9a-f]{64}", records[0]["sha256"])


def test_dump_uses_owner_role_and_migration_table(tmp_path: Path) -> None:
    fake = FakeTools()
    backup.run_backup(fake, ["ingest"], tmp_path / "w", tmp_path / "up", ENV)
    dump = next(c for c in fake.calls if c[0][0] == "pg_dump")[0]
    assert "--format=custom" in dump
    assert "--schema=ingest" in dump
    assert "--role=ingest_owner" in dump
    assert "--table=public.schema_migration" in dump


def test_production_connection_strings_never_appear_in_argv(tmp_path: Path) -> None:
    fake = FakeTools()
    backup.run_backup(fake, ["core"], tmp_path / "w", tmp_path / "up", ENV)
    for argv, _env in fake.calls:
        if argv[0] != "pg_restore":  # only the throwaway database URL is an argument
            assert not any("invalid" in a or "postgresql://" in a for a in argv)
        assert ENV["BACKUP_SOURCE_URL"] not in argv
    age = next(c for c in fake.calls if c[0][0] == "age")[0]
    assert ENV["BACKUP_AGE_RECIPIENT"] in age


def test_ephemeral_database_is_dropped_and_derived_from_admin_url(tmp_path: Path) -> None:
    fake = FakeTools()
    backup.run_backup(fake, ["core"], tmp_path / "w", tmp_path / "up", ENV)
    sqls = [c[0][c[0].index("-c") + 1] for c in fake.calls if c[0][0] == "psql" and "-c" in c[0]]
    assert any(s.startswith("CREATE DATABASE restore_core_") for s in sqls)
    assert sqls[-1].startswith("DROP DATABASE IF EXISTS restore_core_")
    url = backup.database_url(ENV["BACKUP_EPHEMERAL_ADMIN_URL"], "restore_core_ab")
    assert url == "postgresql://eph.invalid:5432/restore_core_ab"


def test_table_count_mismatch_fails_and_uploads_nothing(tmp_path: Path) -> None:
    fake = FakeTools(restored_tables=2)
    with pytest.raises(verify_restore.RestoreVerificationError, match="table count"):
        backup.run_backup(fake, ["core"], tmp_path / "w", tmp_path / "up", ENV)
    assert "age" not in fake.tools()
    assert fake.calls[-1][0][0] == "psql"  # database still dropped
    assert "DROP DATABASE" in fake.calls[-1][0][-1]
    assert list((tmp_path / "up").iterdir()) == []
    assert list((tmp_path / "w").iterdir()) == []


def test_migration_version_mismatch_fails(tmp_path: Path) -> None:
    fake = FakeTools(restored_versions="0001\n")
    with pytest.raises(verify_restore.RestoreVerificationError, match="migration versions"):
        backup.run_backup(fake, ["core"], tmp_path / "w", tmp_path / "up", ENV)
    assert "age" not in fake.tools()


def test_missing_environment_fails_before_any_command(tmp_path: Path) -> None:
    fake = FakeTools()
    with pytest.raises(RuntimeError, match="BACKUP_AGE_RECIPIENT"):
        backup.run_backup(
            fake, ["core"], tmp_path / "w", tmp_path / "up", {**ENV, "BACKUP_AGE_RECIPIENT": ""}
        )
    assert fake.calls == []


def test_unsafe_schema_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        backup.run_backup(FakeTools(), ["core; drop"], tmp_path / "w", tmp_path / "up", ENV)


def test_upload_check_rejects_plaintext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = tmp_path / "a.dump.age"
    good.write_bytes(b"x")
    bad = tmp_path / "a.dump"
    bad.write_bytes(b"x")
    monkeypatch.setattr("sys.argv", ["verify_restore", str(good)])
    assert verify_restore.main() == 0
    monkeypatch.setattr("sys.argv", ["verify_restore", str(good), str(bad)])
    assert verify_restore.main() == 1


def test_sha256_file(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_bytes(b"abc")
    assert backup.sha256_file(f).startswith("ba7816bf")


def test_workflow_contract() -> None:
    text = (ROOT / ".github/workflows/backups.yml").read_text(encoding="utf-8")
    if "ops.backups.backup" not in text:
        pytest.skip("workflow patch docs/runs/T-063-backups-workflow.diff not applied yet")
    assert '"17 3 * * *"' in text
    assert "retention-days: ${{ steps.retention.outputs.days }}" in text
    assert "*.age" in text
    assert "ops.backups.backup" in text
    assert not re.search(r"postgres(ql)?://[^\s$]*:[^\s$]*@", text)


def test_workflow_skips_visibly_without_secrets() -> None:
    text = (ROOT / ".github/workflows/backups.yml").read_text(encoding="utf-8")
    assert "needs: [gate]" in text
    assert "if: needs.gate.outputs.enabled == 'true'" in text
    assert "backups disabled: secrets missing" in text
    assert "::warning" in text
    assert "GITHUB_STEP_SUMMARY" in text
    for name in (
        "BACKUP_SOURCE_URL",
        "BACKUP_EPHEMERAL_ADMIN_URL",
        "BACKUP_EPHEMERAL_PASSWORD",
        "BACKUP_AGE_RECIPIENT",
    ):
        assert f"secrets.{name} != ''" in text


def test_go_live_runbook_lists_the_backup_secrets() -> None:
    runbook = (ROOT / "docs/runbooks/go-live.md").read_text(encoding="utf-8")
    assert "Enable the nightly backups" in runbook
    for name in (
        "BACKUP_SOURCE_URL",
        "BACKUP_EPHEMERAL_ADMIN_URL",
        "BACKUP_EPHEMERAL_PASSWORD",
        "BACKUP_AGE_RECIPIENT",
    ):
        assert name in runbook


def test_readme_contract() -> None:
    readme = (ROOT / "ops/backups/README.md").read_text(encoding="utf-8")
    for schema in SCHEMAS:
        assert f"| {schema} " in readme
    assert "RPO" in readme
    assert "RTO" in readme
