"""Fail when a restricted owner-only asset is tracked or its ignore rules are weakened.

The check looks at path names only and never opens any file. It asks git for the
tracked paths and verifies that git ignores the restricted names at the root and at
depth. It relates to ``.gitignore``, ``.claude/conventions/restricted-assets.md``,
``Makefile``, and ``.github/workflows/ci.yml``. Its purpose is to keep the owner-only
launcher out of every branch that managed agents clone.
"""

import re
import subprocess
import sys
from pathlib import Path

RESTRICTED_PATH = re.compile(r"(^|/)(launch|launhc)_managed_run(\.|$)", re.IGNORECASE)
PROBE_PATHS = (
    "launch_managed_run.py",
    "launhc_managed_run.py",
    "sub/dir/launch_managed_run.py",
    "launch_managed_run.pyc",
)


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *arguments], cwd=repository, capture_output=True, check=False)


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    listing = _git(repository, "ls-files", "-z")
    if listing.returncode != 0:
        print("restricted asset guard: cannot list tracked files", file=sys.stderr)
        return 1
    failures: list[str] = []
    tracked = [name for name in listing.stdout.decode("utf-8").split("\0") if name]
    if any(RESTRICTED_PATH.search(name) for name in tracked):
        failures.append("a restricted asset is tracked by git")
    for probe in PROBE_PATHS:
        if _git(repository, "check-ignore", "-q", probe).returncode != 0:
            failures.append(f"git does not ignore the probe path {probe}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("Restricted asset guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
