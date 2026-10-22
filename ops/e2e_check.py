"""Check that demo data reached the schemas of the running Compose stack (the e2e stage).

Run it after ``make invent-tracker``. It counts rows through ``docker compose exec postgres psql``
as the local container superuser, so it never reads or prints a password, and it polls because the
outbox relay and the consumers fill the projections after the seed returns. Required tables must
hold rows (the seed scores and briefs every demo company, ``ops/seed/enrich.py``), the outbox
must have no unpublished event, and the dead letter table must stay empty;
the other tables are printed as information (``--strict`` requires them too). Exit code 1 means a
requirement failed after ``--timeout`` seconds. It relates to the Makefile, ``docker-compose.yml``,
``ops/seed`` and the e2e job in ``.github/workflows/ci.yml``.
"""

import argparse
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

PSQL = 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -v ON_ERROR_STOP=1 -c "$0"'
PENDING_OUTBOX = "SELECT count(*) FROM core.outbox_event WHERE published_at IS NULL"
DEAD_LETTERS = "SELECT count(*) FROM core.outbox_dead_letter"
DEAD_LETTER_ERRORS = (
    "SELECT left(error, 160) || ' x' || count(*) FROM core.outbox_dead_letter "
    "GROUP BY error ORDER BY count(*) DESC LIMIT 5"
)


@dataclass(frozen=True)
class Table:
    """A table the demo data should fill; ``required`` makes an empty table a failure."""

    name: str
    required: bool


TABLES = (
    Table("core.company", True),
    Table("core.signal", True),
    Table("pulse.fact_signal", True),
    Table("pulse.daily_rollup", True),
    Table("ml.lead_score", True),
    Table("brief.record_digest", True),
    # core.brief is keyed by project and no demo step creates a project; raw_snapshot needs ingest.
    Table("core.brief", False),
    Table("ingest.raw_snapshot", False),
)

Query = Callable[[str], int]
DeadLetterErrors = Callable[[], list[str]]


def compose_text(sql: str) -> str:
    """Run one SQL query inside the postgres container and return its unaligned output."""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c", PSQL, sql],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def compose_query(sql: str) -> int:
    """Run one scalar SQL query inside the postgres container and return the integer."""
    return int(compose_text(sql))


def dead_letter_errors() -> list[str]:
    """The most common dead letter errors with their counts (error texts hold no credentials)."""
    return [line for line in compose_text(DEAD_LETTER_ERRORS).splitlines() if line]


def collect(query: Query, tables: Sequence[Table]) -> dict[str, int]:
    """Row counts per table plus the two outbox counters."""
    counts = {table.name: query(f"SELECT count(*) FROM {table.name}") for table in tables}
    counts["outbox pending"] = query(PENDING_OUTBOX)
    counts["outbox dead letters"] = query(DEAD_LETTERS)
    return counts


def failures(counts: dict[str, int], tables: Sequence[Table], *, strict: bool) -> list[str]:
    """Human readable reasons the counts do not meet the requirements."""
    problems = [
        f"{table.name} is empty"
        for table in tables
        if (table.required or strict) and counts[table.name] == 0
    ]
    if counts["outbox pending"]:
        problems.append(f"{counts['outbox pending']} outbox events are still unpublished")
    if counts["outbox dead letters"]:
        problems.append(f"{counts['outbox dead letters']} events went to the dead letter table")
    return problems


def run(
    argv: Sequence[str] | None = None,
    query: Query = compose_query,
    sleep: Callable[[float], None] = time.sleep,
    errors: DeadLetterErrors = dead_letter_errors,
) -> int:
    """Poll until the requirements hold or the timeout passes; print the final counts."""
    parser = argparse.ArgumentParser(description="Check demo data in the running stack.")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--strict", action="store_true", help="require every table, not only core")
    args = parser.parse_args(argv)

    deadline = time.monotonic() + args.timeout
    while True:
        counts = collect(query, TABLES)
        problems = failures(counts, TABLES, strict=args.strict)
        if not problems or time.monotonic() >= deadline:
            break
        sleep(args.interval)

    for table in TABLES:
        needed = "required" if table.required or args.strict else "info"
        print(f"{table.name}: {counts[table.name]} ({needed})")
    print(f"outbox pending: {counts['outbox pending']}")
    print(f"outbox dead letters: {counts['outbox dead letters']}")
    if counts["outbox dead letters"]:
        for line in errors():
            print(f"dead letter error: {line}")
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    print("e2e data check: " + ("FAILED" if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(run())
