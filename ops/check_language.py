"""Enforce the repository's English-only text and naming convention.

The checker scans UTF-8 text files outside generated caches, rejects non-ASCII
characters, and rejects a curated list of Spanish words and legacy identifiers.
It relates to ``Makefile`` and ``.github/workflows/ci.yml`` and complements
``ops/check_script_docs.py``. Its purpose is to keep docstrings, comments,
configuration descriptions, messages, and code conventions in English. The Spanish
ADR exports under ``docs/adr`` are the only excluded files. The frontend design components
(``frontend/*.dc.html``) and the generated dc runtime (``frontend/support.js``) may hold
non-ASCII text, because they carry the Spanish UI copy and a vendored file that must not be
edited; the legacy-identifier check still applies to them.
"""

import re
import subprocess
import sys
from pathlib import Path

EXCLUDED_PARTS = {".git", "__pycache__", ".mypy_cache", ".ruff_cache"}
# The architecture decision records are exported verbatim from the project vault in Spanish.
EXCLUDED_DIRECTORIES = (Path("docs") / "adr",)
# Spanish UI copy and the generated dc runtime; frontend/api.js and the tests stay ASCII.
NON_ASCII_ALLOWED = re.compile(r"^frontend/(?:[^/]+\.dc\.html|support\.js)$")
LEGACY_IDENTIFIER_HEX = (
    "636f6e73756c746f7269615f7377",
    "616e616c69746963615f6264",
    "6361746572696e675f6576656e746f73",
    "656d70726573612d73696e746574696361",
    "6d61726b6574696e675f6469676974616c",
)
LEGACY_IDENTIFIERS = tuple(bytes.fromhex(value).decode("ascii") for value in LEGACY_IDENTIFIER_HEX)
MARKER_PATTERN = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(map(re.escape, LEGACY_IDENTIFIERS)) + r")(?![A-Za-z])",
    re.IGNORECASE,
)


def candidate_files(repository: Path) -> list[Path] | None:
    """List files git tracks or would show as untracked, never ignored ones.

    Returns ``None`` when git cannot enumerate, so the guard fails closed instead of
    walking the directory tree and opening files that the repository ignores.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    names = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    return sorted(repository / name for name in names)


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    candidates = candidate_files(repository)
    if candidates is None:
        print("English language guard: cannot list files through git", file=sys.stderr)
        return 1
    for path in candidates:
        if not path.is_file() or EXCLUDED_PARTS.intersection(path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(repository)
        if any(relative.is_relative_to(excluded) for excluded in EXCLUDED_DIRECTORIES):
            continue
        ascii_only = not NON_ASCII_ALLOWED.match(relative.as_posix())
        for line_number, line in enumerate(content.splitlines(), 1):
            if ascii_only and any(ord(character) > 127 for character in line):
                failures.append(f"{relative}:{line_number}: non-ASCII text")
            if MARKER_PATTERN.search(line):
                failures.append(f"{relative}:{line_number}: non-English marker")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("English language guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
