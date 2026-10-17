"""Define the backup command contract for the manual workflow.

The script creates an ``f0_scaffold`` manifest and marks where the production
``pg_dump`` implementation will run; it uses no credentials and is not a real
backup. It relates to ``.github/workflows/backups.yml``,
``ops/backups/verify_restore.py``, and ``restore.sh``. It is used to check the
runbook wiring until Supabase, ephemeral Postgres, and ``age`` are connected.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schema", action="append", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "status": "f0_scaffold",
        "created_at": datetime.now(UTC).isoformat(),
        "schemas": args.schema,
        "output": str(args.output),
    }
    for schema in args.schema:
        print(f"backup placeholder: pg_dump --schema={schema} -> {args.output}")
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
