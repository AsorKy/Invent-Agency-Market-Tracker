"""Review migrations to block accidental destructive changes.

The script scans SQL files in ``migrations/`` and only permits DROP, RENAME,
ALTER TYPE, or destructive NOT NULL changes in files whose names start with
``contract_``. It relates to ``migrations/README.md``, CI, and the Postgres
bootstrap in ``docker-compose.yml``. It provides a low-cost guardrail before
changes reach the shared schema.
"""

import re
import sys
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
DESTRUCTIVE = re.compile(
    r"ALTER\s+TABLE.*\b(DROP|RENAME)\b|ALTER\s+TYPE|ALTER\s+TABLE.*"
    r"ALTER\s+COLUMN.*\bSET\s+NOT\s+NULL\b",
    re.IGNORECASE | re.DOTALL,
)


def main() -> int:
    failures: list[str] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        if not path.name.startswith("contract_") and DESTRUCTIVE.search(sql):
            failures.append(f"{path.name}: potentially destructive change outside contract_")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("Migration guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
