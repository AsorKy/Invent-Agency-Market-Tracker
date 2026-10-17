"""Validate the manifest produced by the backup scaffold.

The command iterates over the provided manifests and returns a non-zero exit
code when a file is missing, JSON is invalid, or the status is unsupported. It
relates to ``ops/backups/backup.py``, ``restore.sh``, and the backup workflow;
it is used as a CI contract until a real PostgreSQL restore exists.
"""

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", nargs="+")
    args = parser.parse_args()
    failures = 0
    for dump in args.dump:
        path = Path(dump)
        if not path.exists():
            print(f"restore verification failed: missing {path}")
            failures += 1
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"restore verification failed: {path}: {exc}")
            failures += 1
            continue
        if manifest.get("status") == "f0_scaffold":
            print(f"restore verification scaffold accepted: {path}")
        else:
            print(f"restore verification failed: unsupported manifest {path}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
