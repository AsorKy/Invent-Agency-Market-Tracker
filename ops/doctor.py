"""Report the local configuration and service health without printing values.

``make doctor`` (REQ-RUN-005) lists, by variable name only, which credentials
and settings are set or missing, which ingest sources are active and in which
mode, the health of each service as seen through the gateway ``/ready``, and
whether the configured webhook URLs answer. Values and URLs are never printed
because they can hold secrets. It reads ``.env`` and the process environment
and relates to ``ops/bootstrap_env.py`` and ``libs/common/settings.py``.
Exit code 1 means a required variable is missing.
"""

import argparse
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
from ops.e2e_check import (  # noqa: E402
    COMPANIES_UNSCORED,
    DEAD_LETTERS,
    PENDING_OUTBOX,
    SCORES_WITHOUT_FEATURES,
    compose_query,
)

OLDEST_UNPUBLISHED_SECONDS = (
    "SELECT COALESCE(floor(extract(epoch FROM now() - min(created_at)))::bigint, 0) "
    "FROM core.outbox_event WHERE published_at IS NULL"
)
AUTO_QUALIFIED_LEADS = "SELECT count(*) FROM core.lead WHERE source = 'auto_qualification'"
DEFAULT_MAX_BACKLOG_SECONDS = 300

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


def pipeline_report(
    query: Callable[[str], int] = compose_query,
    max_backlog_seconds: int = DEFAULT_MAX_BACKLOG_SECONDS,
) -> tuple[list[str], bool]:
    """Return the pipeline count lines and whether the pipeline is healthy.

    Read only and counts only. Unhealthy means dead letters above zero or the oldest
    unpublished outbox event older than ``max_backlog_seconds``.
    """
    pending = query(PENDING_OUTBOX)
    oldest = query(OLDEST_UNPUBLISHED_SECONDS)
    dead = query(DEAD_LETTERS)
    lines = [
        "",
        "Pipeline (counts only)",
        f"  outbox unpublished: {pending}",
        f"  outbox oldest unpublished age seconds: {oldest}",
        f"  outbox dead letters: {dead}",
        f"  companies without a score: {query(COMPANIES_UNSCORED)}",
        f"  score rows without stored features: {query(SCORES_WITHOUT_FEATURES)}",
        f"  leads from auto qualification: {query(AUTO_QUALIFIED_LEADS)}",
    ]
    problems = []
    if dead > 0:
        problems.append("dead letters above zero")
    if oldest > max_backlog_seconds:
        problems.append(f"oldest unpublished event older than {max_backlog_seconds} seconds")
    if problems:
        lines.append("  UNHEALTHY: " + "; ".join(problems))
    return lines, not problems


def main(
    argv: Sequence[str] | None = None,
    query: Callable[[str], int] = compose_query,
) -> int:
    """Print the report; return 1 when a variable is missing or the pipeline is unhealthy."""
    parser = argparse.ArgumentParser(description="Report configuration and pipeline health.")
    parser.add_argument("--pipeline", action="store_true", help="also report the data pipeline")
    parser.add_argument(
        "--max-backlog-seconds", type=int, default=DEFAULT_MAX_BACKLOG_SECONDS, metavar="N"
    )
    args = parser.parse_args(argv if argv is not None else [])
    lines, ok = build_report(collect_values(ROOT / ".env", os.environ))
    if args.pipeline:
        try:
            extra, healthy = pipeline_report(query, args.max_backlog_seconds)
        except (RuntimeError, ValueError, OSError) as error:
            extra, healthy = ["", f"Pipeline: query failed ({type(error).__name__})"], False
        lines += extra
        ok = ok and healthy
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
