"""Review migrations to block accidental destructive changes.

The guardian scans SQL files in ``migrations/`` and only permits DROP, RENAME,
ALTER TYPE, column type changes, or NOT NULL without a default in files whose
names start with ``contract_``. It also rejects CREATE SCHEMA for a schema that
is not in ``ops/schemas.py`` and, with ``--base <git-ref>``, a ``contract_``
file changed in the same range as application code. Relates to
``migrations/README.md``, ADR sections 8.2 and 17.3, REQ-DAT-006 and REQ-EXT-003.
"""

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops.schemas import CATALOG_SCHEMAS, LEGACY_SCHEMAS  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "migrations"
FLAGS = re.IGNORECASE | re.DOTALL

STATEMENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("DROP", re.compile(r"^\s*DROP\b", FLAGS)),
    ("DROP in ALTER", re.compile(r"^\s*ALTER\b.*\bDROP\b", FLAGS)),
    ("RENAME", re.compile(r"^\s*ALTER\b.*\bRENAME\b", FLAGS)),
    ("ALTER TYPE", re.compile(r"^\s*ALTER\s+TYPE\b", FLAGS)),
    (
        "column type change",
        re.compile(
            r"^\s*ALTER\s+TABLE\b.*\bALTER\s+(?:COLUMN\s+)?\w+\s+(?:SET\s+DATA\s+)?TYPE\b", FLAGS
        ),
    ),
    ("SET NOT NULL", re.compile(r"^\s*ALTER\s+TABLE\b.*\bSET\s+NOT\s+NULL\b", FLAGS)),
)
ADD_CLAUSE = re.compile(r"^\s*ADD\s+(?!CONSTRAINT\b|PRIMARY\b|FOREIGN\b|UNIQUE\b|CHECK\b)", FLAGS)
CLAUSE_SPLIT = re.compile(r",\s*(?=(?:ADD|ALTER|DROP|RENAME)\b)", FLAGS)
CREATE_SCHEMA = re.compile(r"^\s*CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", FLAGS)
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}
NON_CODE_DIRS = {"tests", "docs", "ops", "migrations"}


def strip_comments(sql: str) -> str:
    """Remove ``--`` line comments and ``/* */`` block comments."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def check_sql(name: str, sql: str, allowed_schemas: Iterable[str] = CATALOG_SCHEMAS) -> list[str]:
    """Return the guardian failures of one migration file."""
    contract = name.startswith("contract_")
    allowed = set(allowed_schemas) | LEGACY_SCHEMAS
    failures: list[str] = []
    for statement in strip_comments(sql).split(";"):
        match = CREATE_SCHEMA.match(statement)
        if match and match.group(1).lower() not in allowed:
            failures.append(f"{name}: schema {match.group(1)} is not in ops/schemas.py")
        if contract:
            continue
        for label, rule in STATEMENT_RULES:
            if rule.search(statement):
                failures.append(f"{name}: {label} outside contract_")
        if re.match(r"^\s*ALTER\s+TABLE\b", statement, FLAGS):
            for column in CLAUSE_SPLIT.split(statement):
                added = re.sub(r"^.*?(?=\bADD\b)", "", column, count=1, flags=FLAGS)
                if (
                    ADD_CLAUSE.search(added)
                    and re.search(r"\bNOT\s+NULL\b", added, FLAGS)
                    and not re.search(r"\bDEFAULT\b|\bGENERATED\b", added, FLAGS)
                ):
                    failures.append(f"{name}: NOT NULL without default outside contract_")
    return failures


def check_directory(
    migrations: Path = MIGRATIONS, allowed_schemas: Iterable[str] = CATALOG_SCHEMAS
) -> list[str]:
    """Run ``check_sql`` over every ``*.sql`` file of a migrations directory."""
    failures: list[str] = []
    schemas = tuple(allowed_schemas)
    for path in sorted(migrations.glob("*.sql")):
        failures += check_sql(path.name, path.read_text(encoding="utf-8"), schemas)
    return failures


def changed_files(base: str, repo: Path = REPO) -> list[str]:
    """Return the paths changed between ``base`` and HEAD plus the working tree."""
    out = subprocess.run(
        ["git", "diff", "--name-only", base],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def is_application_code(path: str) -> bool:
    """Return True for source files that are not tests, docs, ops, or migrations."""
    parts = Path(path).parts
    return Path(path).suffix in CODE_SUFFIXES and parts[0] not in NON_CODE_DIRS


def check_commit_range(base: str, repo: Path = REPO) -> list[str]:
    """Reject a ``contract_`` migration changed together with application code."""
    files = changed_files(base, repo)
    contracts = [
        f for f in files if f.startswith("migrations/") and Path(f).name.startswith("contract_")
    ]
    code = [f for f in files if is_application_code(f)]
    if contracts and code:
        return [f"{contracts[0]}: contract_ migration mixed with application code ({code[0]}, ...)"]
    return []


def main(argv: list[str] | None = None) -> int:
    """Run the guardian and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="git ref to diff against for the contract_ mixing rule")
    parser.add_argument("--migrations-dir", type=Path, default=MIGRATIONS)
    args = parser.parse_args(argv)
    failures = check_directory(args.migrations_dir)
    if args.base:
        failures += check_commit_range(args.base)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("Migration guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
