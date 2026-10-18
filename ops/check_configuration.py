"""Check configuration names shared by Settings, Compose, and ``.env.example``.

The command extracts fields from ``libs.common.settings.Settings`` and variable
interpolations from ``docker-compose.yml``; it verifies that both are documented
in ``.env.example``. It relates to ``Makefile``, CI, and every service process.
Its purpose is to catch renamed, missing, or undocumented runtime variables
before deployment configuration drifts. It also scans tracked text files for
secret-like values (REQ-SEC-004) and reports only file names and match counts,
never the matched text.
"""

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "libs/common/settings.py"
COMPOSE_PATH = ROOT / "docker-compose.yml"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
COMPOSE_VARIABLE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"://[^/\s:@$]+:[^/\s:@$<{]{6,}@(?!localhost|postgres|redis|db\b)[A-Za-z0-9.-]+\."),
)


def list_repository_files(root: Path) -> list[Path]:
    """Return tracked and untracked-but-not-ignored files, as git lists them."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    names = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    return [root / name for name in names]


def scan_for_secrets(paths: list[Path]) -> dict[Path, int]:
    """Count secret-like matches per text file; binary or unreadable files are skipped."""
    findings: dict[Path, int] = {}
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="ignore")
        count = sum(len(pattern.findall(text)) for pattern in SECRET_PATTERNS)
        if count:
            findings[path] = count
    return findings


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
    findings = scan_for_secrets(list_repository_files(ROOT))
    if findings:
        print("Secret-like values found (names and counts only):", file=sys.stderr)
        for path, count in sorted(findings.items()):
            print(f"{path.relative_to(ROOT).as_posix()}: {count}", file=sys.stderr)
        return 1
    print(f"Configuration guard: OK ({len(required)} variables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
