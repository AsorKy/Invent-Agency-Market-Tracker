"""Create verified, encrypted per-schema backups.

For each schema the command runs ``pg_dump --format=custom`` with the schema
owner role, records the SHA-256 of the dump, restores it into an ephemeral
database and checks migration versions and table counts
(``ops/backups/verify_restore.py``), encrypts it with ``age`` for a public
recipient, and removes the plaintext. Only ``*.age`` files land in the upload
directory. It relates to ``.github/workflows/backups.yml``, ``restore.sh``, and
REQ-BAK-001. Connection strings come from ``BACKUP_SOURCE_URL`` and
``BACKUP_EPHEMERAL_ADMIN_URL`` (the throwaway database URL is derived from it)
and the public recipient from ``BACKUP_AGE_RECIPIENT``. Values are never printed;
the source URL never reaches a command line. ``--retention-days`` prints 28 on
Sunday, else 7.
"""

import argparse
import hashlib
import os
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ops.backups.verify_restore import Runner, verify_restore
from ops.schemas import SCHEMAS, owner_role, validate_identifier

DAILY_RETENTION_DAYS = 7
SUNDAY_RETENTION_DAYS = 28
SUNDAY = 6
TIMEOUT_SECONDS = 1800
REQUIRED_ENV = (
    "BACKUP_SOURCE_URL",
    "BACKUP_EPHEMERAL_ADMIN_URL",
    "BACKUP_AGE_RECIPIENT",
)


def retention_days(day: date) -> int:
    """Return artifact retention: 28 days for Sunday backups, 7 otherwise."""
    return SUNDAY_RETENTION_DAYS if day.weekday() == SUNDAY else DAILY_RETENTION_DAYS


def database_url(admin_url: str, database: str) -> str:
    """Return ``admin_url`` pointing at ``database`` (same host and credentials)."""
    return urlunsplit(urlsplit(admin_url)._replace(path=f"/{database}"))


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def subprocess_runner(argv: Sequence[str], env: Mapping[str, str]) -> str:
    """Run ``argv`` with a finite timeout, extra env, and return stdout."""
    merged = {**os.environ, **env}
    result = subprocess.run(  # noqa: S603
        list(argv), env=merged, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"{argv[0]} failed with exit code {result.returncode}")
    return result.stdout


def backup_schema(
    run: Runner,
    schema: str,
    work_dir: Path,
    upload_dir: Path,
    env: Mapping[str, str],
    stamp: str,
) -> dict[str, str]:
    """Dump, verify, and encrypt one schema; return its sha256 record."""
    owner = owner_role(validate_identifier(schema))
    dump = work_dir / f"{schema}-{stamp}.dump"
    try:
        run(
            [
                "pg_dump",
                "--format=custom",
                f"--schema={schema}",
                "--table=public.schema_migration",
                f"--role={owner}",
                f"--file={dump}",
            ],
            {"PGDATABASE": env["BACKUP_SOURCE_URL"]},
        )
        digest = sha256_file(dump)
        database = f"restore_{schema}_{uuid.uuid4().hex[:8]}"
        verify_restore(
            run,
            dump,
            schema,
            source_env={"PGDATABASE": env["BACKUP_SOURCE_URL"]},
            admin_env={"PGDATABASE": env["BACKUP_EPHEMERAL_ADMIN_URL"]},
            ephemeral_env={"PGDATABASE": database_url(env["BACKUP_EPHEMERAL_ADMIN_URL"], database)},
            database=database,
        )
        encrypted = upload_dir / f"{dump.name}.age"
        run(
            [
                "age",
                "--recipient",
                env["BACKUP_AGE_RECIPIENT"],
                "--output",
                str(encrypted),
                str(dump),
            ],
            {},
        )
    finally:
        dump.unlink(missing_ok=True)
    return {"schema": schema, "sha256": digest, "artifact": encrypted.name}


def run_backup(
    run: Runner,
    schemas: Sequence[str],
    work_dir: Path,
    upload_dir: Path,
    env: Mapping[str, str],
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Back up every schema; any failure aborts before further uploads."""
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)}")
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    work_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for schema in schemas:
        record = backup_schema(run, schema, work_dir, upload_dir, env, stamp)
        print(f"backup ok: {schema} sha256={record['sha256']} -> {record['artifact']}")
        records.append(record)
    return records


def main() -> int:
    """Parse arguments and run the backup, or print the retention days."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/invent-backup-work"))
    parser.add_argument("--output", type=Path, help="upload directory for .age files")
    parser.add_argument("--schema", action="append", help="repeat; default is all schemas")
    parser.add_argument("--retention-days", action="store_true")
    args = parser.parse_args()
    if args.retention_days:
        print(retention_days(datetime.now(UTC).date()))
        return 0
    if args.output is None:
        parser.error("--output is required")
    try:
        run_backup(
            subprocess_runner, args.schema or list(SCHEMAS), args.work_dir, args.output, os.environ
        )
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
