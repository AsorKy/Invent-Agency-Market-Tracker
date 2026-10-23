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
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import yaml  # type: ignore[import-untyped]

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libs.common.internal_auth import LOCAL_ENVIRONMENTS  # noqa: E402
from ops.schemas import SCHEMAS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "libs/common/settings.py"
COMPOSE_PATH = ROOT / "docker-compose.yml"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
COMPOSE_VARIABLE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")
# Every service that signs or validates service tokens (REQ-SEC-005) needs the secret.
TOKEN_SERVICES = (
    "gateway",
    "ingest",
    "ingest-processor",
    "core",
    "brief",
    "identity",
    "pulse",
    "ml",
)
# Identity reads its database settings from these (services/identity/project/settings.py).
IDENTITY_DB_VARIABLES = ("POSTGRES_HOST", "IDENTITY_DB_USER", "IDENTITY_DB_PASSWORD")
# Redis ACL user each service must connect as (REQ-SEC-005); never the default user.
REDIS_USER_BY_SERVICE = {"gateway": "edge"}
REDIS_DATA_SERVICES = (
    "ingest",
    "ingest-processor",
    "core",
    "core-relay",
    "core-worker",
    "pulse",
    "pulse-consumer",
    "ml",
    "ml-consumer",
    "brief",
)
NON_PRODUCTION_PREFIXES = ("SEED_", "VERIFY_LIVE_")
CREDENTIAL_SUFFIXES = ("_TOKEN", "_SECRET", "_KEY", "_PASSWORD", "_CREDENTIAL")
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


def services_missing_service_token(compose_text: str) -> list[str]:
    """Return the token services whose Compose environment lacks INTERNAL_SERVICE_TOKEN."""
    services = (yaml.safe_load(compose_text) or {}).get("services", {})
    missing = []
    for name in TOKEN_SERVICES:
        environment = (services.get(name) or {}).get("environment") or {}
        if "INTERNAL_SERVICE_TOKEN" not in environment:
            missing.append(name)
    return missing


def role_wiring_problems(compose_text: str, env_variables: set[str]) -> list[str]:
    """Return problems in the identity and bootstrap-roles database wiring (REQ-SEC-005)."""
    services = (yaml.safe_load(compose_text) or {}).get("services", {})
    problems = []
    identity_env = (services.get("identity") or {}).get("environment") or {}
    for name in IDENTITY_DB_VARIABLES:
        if name not in identity_env:
            problems.append(f"identity lacks {name} in docker-compose.yml")
        if name not in env_variables:
            problems.append(f"{name} missing in .env.example")
    bootstrap_env = (services.get("bootstrap-roles") or {}).get("environment") or {}
    for schema in SCHEMAS:
        name = f"BOOTSTRAP_PASSWORD_{schema.upper()}"
        if name not in bootstrap_env:
            problems.append(f"bootstrap-roles lacks {name} in docker-compose.yml")
        if name not in env_variables:
            problems.append(f"{name} missing in .env.example")
    return problems


def redis_acl_problems(compose_text: str) -> list[str]:
    """Return services whose REDIS_URL lacks the expected ACL user, plus redis wiring gaps."""
    services = (yaml.safe_load(compose_text) or {}).get("services", {})
    expected = {**REDIS_USER_BY_SERVICE, **dict.fromkeys(REDIS_DATA_SERVICES, "data")}
    problems = []
    for name, user in expected.items():
        environment = (services.get(name) or {}).get("environment") or {}
        url = str(environment.get("REDIS_URL", ""))
        if urlsplit(url).username != user:
            problems.append(f"{name} must use REDIS_URL with Redis ACL user {user}")
    redis = services.get("redis") or {}
    redis_env = redis.get("environment") or {}
    for name in ("REDIS_EDGE_PASSWORD", "REDIS_DATA_PASSWORD"):
        if name not in redis_env:
            problems.append(f"redis lacks {name} in docker-compose.yml")
    if "entrypoint.sh" not in " ".join(str(part) for part in redis.get("entrypoint", [])):
        problems.append("redis must start through deploy/redis/entrypoint.sh (ACL file)")
    return problems


def production_caller_problems(env: Mapping[str, str]) -> list[str]:
    """Return names of seed or verify_live credentials configured for production (D-28).

    The two caller names are refused by ``require_service_token`` in production, so a
    production configuration must not carry variables meant for them. Names only.
    """
    profile = env.get("APP_PROFILE", "local").strip().lower()
    environment = env.get("ENVIRONMENT", "local").strip().lower()
    if profile != "production" and environment in LOCAL_ENVIRONMENTS:
        return []
    return sorted(
        name
        for name, value in env.items()
        if value.strip()
        and name.upper().startswith(NON_PRODUCTION_PREFIXES)
        and name.upper().endswith(CREDENTIAL_SUFFIXES)
    )


def main() -> int:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    env_variables = _env_example_variables()
    required = _settings_variables() | set(COMPOSE_VARIABLE.findall(compose))
    missing = sorted(required - env_variables)
    if missing:
        print("Missing configuration variables in .env.example:", file=sys.stderr)
        print("\n".join(missing), file=sys.stderr)
        return 1
    if "EVENT_CHAIN_EMIT=false" not in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        print("EVENT_CHAIN_EMIT must default to false in .env.example (T-149)", file=sys.stderr)
        return 1
    without_token = services_missing_service_token(compose)
    if without_token:
        print("Services without INTERNAL_SERVICE_TOKEN in docker-compose.yml:", file=sys.stderr)
        print("\n".join(without_token), file=sys.stderr)
        return 1
    wiring = role_wiring_problems(compose, env_variables) + redis_acl_problems(compose)
    if wiring:
        print("Database role and Redis ACL wiring problems:", file=sys.stderr)
        print("\n".join(wiring), file=sys.stderr)
        return 1
    forbidden = production_caller_problems(os.environ)
    if forbidden:
        print("Production must not carry seed or verify_live credentials:", file=sys.stderr)
        print("\n".join(forbidden), file=sys.stderr)
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
