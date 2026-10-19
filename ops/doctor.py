"""Report the local configuration and service health without printing values.

``make doctor`` (REQ-RUN-005) lists, by variable name only, which credentials
and settings are set or missing, which ingest sources are active and in which
mode, the health of each service as seen through the gateway ``/ready``, and
whether the configured webhook URLs answer. Values and URLs are never printed
because they can hold secrets. It reads ``.env`` and the process environment
and relates to ``ops/bootstrap_env.py`` and ``libs/common/settings.py``.
Exit code 1 means a required variable is missing.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ops import bootstrap_env  # noqa: E402

REQUIRED = (
    "POSTGRES_PASSWORD",
    "INTERNAL_SERVICE_TOKEN",
    "BOOTSTRAP_PASSWORD_INGEST",
    "BOOTSTRAP_PASSWORD_CORE",
    "BOOTSTRAP_PASSWORD_PULSE",
    "BOOTSTRAP_PASSWORD_BRIEF",
    "BOOTSTRAP_PASSWORD_ML",
    "DEV_USER_PASSWORD_ADMIN",
    "DEV_USER_PASSWORD_STRATEGIST",
    "DEV_USER_PASSWORD_INTAKE",
    "DEV_USER_PASSWORD_BD_REP",
    "DEV_USER_PASSWORD_ANALYST",
)
LIVE_REQUIRED = ("BRIGHT_DATA_API_TOKEN", "BRIGHT_DATA_WEBHOOK_SECRET")
OPTIONAL = ("EDGE_SECRET", "PUBLIC_WEBHOOK_BASE_URL", "HOT_LEAD_WEBHOOK_URL")
SOURCES = ("MAPS", "COMPUTRABAJO", "LINKEDIN_COMPANIES", "LINKEDIN_JOBS", "INSTAGRAM", "RUES")
WEBHOOKS = ("HOT_LEAD_WEBHOOK_URL", "PUBLIC_WEBHOOK_BASE_URL")

Fetch = Callable[[str], tuple[int, str]]
Reach = Callable[[str], bool]


def fetch(url: str, timeout: float = 3.0) -> tuple[int, str]:
    """Return the HTTP status and body of ``url``; status 0 when unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return int(response.status), response.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read(65536).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return 0, ""


def reachable(url: str) -> bool:
    """Return True when the URL answers with any HTTP status."""
    return fetch(url)[0] > 0


def _is_set(values: Mapping[str, str], name: str) -> bool:
    return bool(values.get(name, "").strip())


def _flag(values: Mapping[str, str], name: str) -> bool:
    return values.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def collect_values(env_file: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Merge ``.env`` with the process environment (the environment wins)."""
    values = bootstrap_env.read_values(env_file) if env_file.exists() else {}
    values.update(environ)
    return values


def build_report(
    values: Mapping[str, str],
    fetch_url: Fetch = fetch,
    reach: Reach = reachable,
) -> tuple[list[str], bool]:
    """Return the report lines and whether every required variable is set."""
    mode = values.get("INGEST_MODE", "mock").strip() or "mock"
    live = mode == "live"
    lines = ["Invent Market Tracker doctor (names only, no values)", "", "Settings"]
    required = REQUIRED + (LIVE_REQUIRED if live else ())
    missing = [name for name in required if not _is_set(values, name)]
    for name in required:
        lines.append(f"  {name}: {'set' if _is_set(values, name) else 'MISSING'}")
    for name in OPTIONAL:
        lines.append(f"  {name}: {'set' if _is_set(values, name) else 'unset (optional)'}")
    lines += ["", f"Ingest mode: {mode}", "Sources"]
    for source in SOURCES:
        enabled = _flag(values, f"SOURCE_{source}_ENABLED")
        dataset = f"SOURCE_{source}_DATASET_ID"
        note = ""
        if enabled and live and not _is_set(values, dataset):
            missing.append(dataset)
            note = f" ({dataset} MISSING)"
        lines.append(f"  {source.lower()}: {'active' if enabled else 'inactive'}{note}")
    lines += ["", "Services (gateway /ready)"]
    gateway = bootstrap_env.service_urls(values)["Gateway readiness"]
    status, body = fetch_url(gateway)
    if status == 0:
        lines.append("  gateway: unreachable (is the stack running?)")
    else:
        lines.append(f"  gateway: {'ready' if status == 200 else 'degraded'}")
        try:
            parsed = json.loads(body)
            dependencies = parsed.get("dependencies", {}) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            dependencies = {}
        for name, ok in sorted(dependencies.items()):
            lines.append(f"  {name}: {'ok' if ok is True else 'DOWN'}")
    lines += ["", "Webhooks"]
    for name in WEBHOOKS:
        if _is_set(values, name):
            lines.append(f"  {name}: {'reachable' if reach(values[name]) else 'UNREACHABLE'}")
        else:
            lines.append(f"  {name}: not configured")
    if missing:
        lines += ["", "Missing: " + ", ".join(sorted(set(missing)))]
    return lines, not missing


def main(argv: Sequence[str] | None = None) -> int:
    """Print the report; return 1 when a required variable is missing."""
    del argv
    lines, ok = build_report(collect_values(ROOT / ".env", os.environ))
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
