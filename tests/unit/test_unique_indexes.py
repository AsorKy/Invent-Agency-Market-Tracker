"""Offline check that every ON CONFLICT target is backed by a unique index.

It scans the SQL embedded in ``services/`` and ``libs/`` and the files in
``migrations/`` without a database. Relates to REQ-DAT-001, REQ-DAT-005 and
``migrations/20261019_0005_unique_indexes.sql``.
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FLAGS = re.IGNORECASE | re.DOTALL

CONFLICT = re.compile(
    r"INSERT\s+INTO\s+([\w.]+)(?:(?!INSERT\s+INTO).)*?"
    r"ON\s+CONFLICT\s*\(([^)]*)\)(?:\s+WHERE\s+(.*?)\s+DO\b)?",
    FLAGS,
)
CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.]+)\s*\(", FLAGS)
UNIQUE_INDEX = re.compile(
    r"CREATE\s+UNIQUE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+([\w.]+)\s*"
    r"\((.*?)\)\s*(?:WHERE\s+(.*?))?\s*$",
    FLAGS,
)
ADD_UNIQUE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w.]+)\s+ADD\s+(?:CONSTRAINT\s+\w+\s+)?"
    r"(?:UNIQUE|PRIMARY\s+KEY)\s*\((.*?)\)",
    FLAGS,
)
Key = tuple[str, tuple[str, ...], str]


def tracked(*suffixes: str, roots: tuple[str, ...]) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [
        REPO / name
        for name in out
        if name.startswith(roots) and name.endswith(suffixes) and (REPO / name).is_file()
    ]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", " ", sql)


def split_top(body: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in body:
        depth += char == "("
        depth -= char == ")"
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    return parts + [current]


def columns(text: str) -> tuple[str, ...]:
    return tuple(norm(part) for part in split_top(text))


def table_body(sql: str, start: int) -> str:
    depth = 1
    for index in range(start, len(sql)):
        depth += sql[index] == "("
        depth -= sql[index] == ")"
        if depth == 0:
            return sql[start:index]
    return sql[start:]


def unique_keys() -> set[Key]:
    keys: set[Key] = set()
    for path in sorted((REPO / "migrations").glob("*.sql")):
        sql = strip_comments(path.read_text(encoding="utf-8"))
        for match in CREATE_TABLE.finditer(sql):
            table = match.group(1).lower()
            for item in split_top(table_body(sql, match.end())):
                item = norm(item)
                column = re.match(r"(\w+) ", item)
                table_level = re.match(
                    r"(?:constraint \w+ )?(?:unique|primary key) ?\((.*)\)", item
                )
                if table_level:
                    keys.add((table, columns(table_level.group(1)), ""))
                elif column and re.search(r"\b(unique|primary key)\b", item):
                    keys.add((table, (column.group(1),), ""))
        for match in ADD_UNIQUE.finditer(sql):
            keys.add((match.group(1).lower(), columns(match.group(2)), ""))
        for statement in sql.split(";"):
            match = UNIQUE_INDEX.search(statement)
            if match:
                predicate = norm(match.group(3) or "")
                keys.add((match.group(1).lower(), columns(match.group(2)), predicate))
    return keys


def conflict_targets() -> list[tuple[str, Key]]:
    found = []
    for path in tracked(".py", ".sql", roots=("services/", "libs/")):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r'"\s*\n\s*"', "", text)
        text = text.replace('" "', " ")
        for match in CONFLICT.finditer(text):
            predicate = norm(match.group(3) or "")
            key = (match.group(1).lower(), columns(match.group(2)), predicate)
            found.append((path.relative_to(REPO).as_posix(), key))
    return found


def backed(key: Key, keys: set[Key]) -> bool:
    table, cols, predicate = key
    return any(
        k_table == table and set(k_cols) == set(cols) and k_pred == predicate
        for k_table, k_cols, k_pred in keys
    )


def test_scanner_finds_known_targets() -> None:
    tables = {key[0] for _, key in conflict_targets()}
    assert {"ingest.raw_snapshot", "core.signal", "pulse.daily_rollup"} <= tables


def test_every_on_conflict_target_has_a_unique_index() -> None:
    keys = unique_keys()
    missing = [
        f"{path}: {key[0]} ({', '.join(key[1])})"
        for path, key in conflict_targets()
        if not backed(key, keys)
    ]
    assert not missing, "ON CONFLICT targets without a unique index: " + "; ".join(missing)


def test_partial_index_needs_matching_predicate() -> None:
    keys = {("core.company", ("tenant_id", "nit"), "nit is not null")}
    assert backed(("core.company", ("tenant_id", "nit"), "nit is not null"), keys)
    assert not backed(("core.company", ("tenant_id", "nit"), ""), keys)


def test_new_migration_adds_required_indexes() -> None:
    keys = unique_keys()
    assert ("core.company", ("tenant_id", "nit"), "nit is not null") in keys
    assert (
        "ingest.failed_fetch",
        ("source", "coalesce(external_id, '')"),
        "dead_lettered_at is null",
    ) in keys
