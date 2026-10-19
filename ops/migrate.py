"""Apply ``migrations/*.sql`` to PostgreSQL in order, one transaction per file.

Each applied file is recorded in ``public.schema_migration`` (version, sha256
checksum, applied_at). A file whose checksum differs from the recorded one is
refused, and a re-run skips every file that is already recorded. Files sort by
name with the ``contract_`` prefix ignored, so ``contract_20261018_0003_*``
runs between ``20261017_0002_*`` and ``20261018_0004_*``. ``--dry-run`` lists
the plan without connecting. Relates to REQ-RUN-002, ADR sections 8.2 and 17.
"""

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "migrations"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS public.schema_migration (
    version text PRIMARY KEY,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""
SELECT_APPLIED = "SELECT version, checksum FROM public.schema_migration"
INSERT_APPLIED = "INSERT INTO public.schema_migration (version, checksum) VALUES ($1, $2)"


class ChecksumError(RuntimeError):
    """Raised when an applied migration file changed after it was recorded."""


def sort_key(path: Path) -> str:
    """Order by version, ignoring the ``contract_`` prefix."""
    name = path.name
    return name.removeprefix("contract_")


def list_migrations(directory: Path = MIGRATIONS) -> list[Path]:
    """Return the migration files in application order."""
    return sorted(directory.glob("*.sql"), key=sort_key)


def checksum(path: Path) -> str:
    """Return the sha256 hex digest of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan(files: list[Path], applied: dict[str, str]) -> list[tuple[Path, str]]:
    """Return ``(file, status)`` pairs; status is ``pending`` or ``applied``.

    Raises ``ChecksumError`` when an applied file no longer matches its record.
    """
    result: list[tuple[Path, str]] = []
    for path in files:
        recorded = applied.get(path.name)
        if recorded is None:
            result.append((path, "pending"))
        elif recorded != checksum(path):
            raise ChecksumError(f"{path.name}: checksum changed since it was applied")
        else:
            result.append((path, "applied"))
    return result


async def migrate(conn: Any, directory: Path = MIGRATIONS, dry_run: bool = False) -> list[str]:
    """Apply pending files through ``conn`` and return the versions applied.

    ``conn`` is an asyncpg-style connection: ``execute``, ``fetch`` and an
    async-context ``transaction()``. In dry-run mode nothing is written.
    """
    files = list_migrations(directory)
    if dry_run:
        try:
            rows = await conn.fetch(SELECT_APPLIED)
        except Exception:  # noqa: BLE001 - table absent on a fresh database
            rows = []
    else:
        await conn.execute(CREATE_TABLE)
        rows = await conn.fetch(SELECT_APPLIED)
    applied = {row["version"]: row["checksum"] for row in rows}
    done: list[str] = []
    for path, status in plan(files, applied):
        if status == "applied":
            continue
        if not dry_run:
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute(INSERT_APPLIED, path.name, checksum(path))
        done.append(path.name)
    return done


async def run(dsn: str, dry_run: bool) -> int:
    """Connect with asyncpg and run the migrations."""
    import asyncpg  # type: ignore[import-untyped]

    conn = await asyncpg.connect(dsn)
    try:
        done = await migrate(conn, dry_run=dry_run)
    except ChecksumError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    finally:
        await conn.close()
    verb = "would apply" if dry_run else "applied"
    for name in done:
        print(f"{verb} {name}")
    if not done:
        print("nothing to apply")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the migrations."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="list pending files only")
    parser.add_argument("--dsn", default=os.environ.get("MIGRATE_DATABASE_URL", ""))
    args = parser.parse_args(argv)
    if args.dry_run and not args.dsn:
        for path in list_migrations():
            print(f"would apply {path.name}")
        return 0
    if not args.dsn:
        print("set MIGRATE_DATABASE_URL or pass --dsn", file=sys.stderr)
        return 2
    return asyncio.run(run(args.dsn, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
