"""Ensure every executable Python module has module documentation.

The script uses ``ast`` to scan ``libs``, ``services``, ``datagen``, and ``ops``
and requires the first node in each file to be a documentation string. It
relates to ``Makefile`` and ``.github/workflows/ci.yml``; SQL, shell, YAML, and
JSON headers remain comments or metadata in their own files. Its purpose is to
prevent new workers, APIs, or tools from lacking their purpose and integrations.
"""

import ast
import sys
from pathlib import Path

ROOTS = ("libs", "services", "datagen", "ops")


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    for root_name in ROOTS:
        for path in sorted((repository / root_name).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError) as exc:
                failures.append(f"{path.relative_to(repository)}: could not parse: {exc}")
                continue
            if not ast.get_docstring(tree, clean=False):
                failures.append(f"{path.relative_to(repository)}: missing module docstring")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("Script documentation guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
