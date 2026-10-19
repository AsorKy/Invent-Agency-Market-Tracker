"""Write ``docs/api/openapi.json`` from the gateway public route registry.

The script renders ``services.gateway.app.openapi_doc.render_openapi`` and writes
the bytes to ``docs/api/openapi.json`` (or the path given as the first argument).
With ``--check`` it writes nothing and exits 1 when the committed file differs.
It relates to ``make openapi`` and ``tests/unit/test_openapi.py``; the frontend
generates its types from the file (ADR-IMT-0001 section 12.6).
"""

import sys
from pathlib import Path

from services.gateway.app.openapi_doc import render_openapi

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.json"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    args = [a for a in argv if a != "--check"]
    target = Path(args[0]) if args else DEFAULT_PATH
    rendered = render_openapi()
    if check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != rendered:
            print(f"{target} is stale; run make openapi", file=sys.stderr)
            return 1
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
