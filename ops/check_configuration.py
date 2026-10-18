"""Check configuration names shared by Settings, Compose, and ``.env.example``.

The command extracts fields from ``libs.common.settings.Settings`` and variable
interpolations from ``docker-compose.yml``; it verifies that both are documented
in ``.env.example``. It relates to ``Makefile``, CI, and every service process.
Its purpose is to catch renamed, missing, or undocumented runtime variables
before deployment configuration drifts.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "libs/common/settings.py"
COMPOSE_PATH = ROOT / "docker-compose.yml"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
COMPOSE_VARIABLE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")


def _settings_variables() -> set[str]:
    tree = ast.parse(SETTINGS_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return {
                item.target.id.upper()
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
    return set()


def _env_example_variables() -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }


def main() -> int:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    env_variables = _env_example_variables()
    required = _settings_variables() | set(COMPOSE_VARIABLE.findall(compose))
    missing = sorted(required - env_variables)
    if missing:
        print("Missing configuration variables in .env.example:", file=sys.stderr)
        print("\n".join(missing), file=sys.stderr)
        return 1
    print(f"Configuration guard: OK ({len(required)} variables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
