"""Check that demo data reached the schemas of the running Compose stack (the e2e stage).

Run it after ``make invent-tracker``. It counts rows through ``docker compose exec postgres psql``
as the local container superuser, so it never reads or prints a password, and it polls because the
outbox relay and the consumers fill the projections after the seed returns. Required tables must
hold rows (the seed scores and briefs every demo company, ``ops/seed/enrich.py``), the outbox
must have no unpublished event, and the dead letter table must stay empty;
every ``core.company`` row must carry a non-null ``score`` (stored by core from
``lead.score_updated``) and every ``ml.lead_score`` row the three stored features; the other
tables are printed as information (``--strict`` requires them too). Exit code 1 means a
requirement failed after ``--timeout`` seconds. Then (T-171) every demo company at or above the
tenant's ``min_score_for_qualified`` (read from ``core.tenant_config``) must have a ``core.lead``
with ``source = 'auto_qualification'``, and ``services.ml.app.backfill`` runs inside the ml
container for the demo tenant, after which no ml.lead_score row may lack features. ``--chain``
(T-164) adds a second step for a stack started with ``EVENT_CHAIN_EMIT=true``: it runs
``--drive-chain`` inside the gateway container
(which holds the service token), creating one signal and moving one lead status through core's
internal API, then requires every chain event type in the outbox published and zero dead
letters. It relates to the Makefile, ``docker-compose.yml``,
``ops/seed`` and the e2e job in ``.github/workflows/ci.yml``.
"""

import argparse
import asyncio
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import httpx

PSQL = 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -v ON_ERROR_STOP=1 -c "$0"'
PENDING_OUTBOX = "SELECT count(*) FROM core.outbox_event WHERE published_at IS NULL"
DEAD_LETTERS = "SELECT count(*) FROM core.outbox_dead_letter"
DEAD_LETTER_ERRORS = (
    "SELECT left(error, 160) || ' x' || count(*) FROM core.outbox_dead_letter "
    "GROUP BY error ORDER BY count(*) DESC LIMIT 5"
)
# core.company.score is core's own copy of the score (migration 0012, core cannot read ml, INV-01);
# the stored features are the three feature_* columns of ml.lead_score (migration 0013).
COMPANIES_UNSCORED = "SELECT count(*) FROM core.company WHERE score IS NULL"
SCORES_WITHOUT_FEATURES = (
    "SELECT count(*) FROM ml.lead_score WHERE feature_icp_fit IS NULL "
    "OR feature_signal_strength IS NULL OR feature_territory_opportunity IS NULL"
)
SCORES_WITH_FEATURES = (
    "SELECT count(*) FROM ml.lead_score WHERE feature_icp_fit IS NOT NULL "
    "AND feature_signal_strength IS NOT NULL AND feature_territory_opportunity IS NOT NULL"
)
# Chain events written to core's outbox; ``signal.scored`` goes to the ML stream (T-159).
CHAIN_TYPES = (
    "signal.tagged",
    "company.matched",
    "signal.explained",
    "project.pipeline_updated",
    "project.forecast_updated",
)
_CHAIN_LIST = ", ".join(f"'{name}'" for name in CHAIN_TYPES)
CHAIN_PUBLISHED = (
    "SELECT count(DISTINCT event_type) FROM core.outbox_event "
    f"WHERE event_type IN ({_CHAIN_LIST}) AND published_at IS NOT NULL"
)
CHAIN_UNPUBLISHED = (
    "SELECT count(*) FROM core.outbox_event "
    f"WHERE event_type IN ({_CHAIN_LIST}) AND published_at IS NULL"
)
BACKFILL_COMMAND = [
    "docker",
    "compose",
    "exec",
    "-T",
    "ml",
    "python",
    "-m",
    "services.ml.app.backfill",
]
DRIVE_COMMAND = ["docker", "compose", "exec", "-T", "gateway", "python", "-m", "ops.e2e_check"]


@dataclass(frozen=True)
class Table:
    """A table the demo data should fill; ``required`` makes an empty table a failure."""

    name: str
    required: bool


TABLES = (
    Table("core.company", True),
    Table("core.signal", True),
    Table("pulse.fact_signal", True),
    Table("pulse.daily_rollup", True),
    Table("ml.lead_score", True),
    Table("brief.record_digest", True),
    # core.brief is keyed by project and no demo step creates a project; raw_snapshot needs ingest.
    Table("core.brief", False),
    Table("ingest.raw_snapshot", False),
)

Query = Callable[[str], int]
Backfill = Callable[[str, int], None]
DeadLetterErrors = Callable[[], list[str]]


def qualified_sql(tenant_id: str, *, missing_lead: bool) -> str:
    """SQL counting demo companies at the qualified threshold (all, or those without a lead).

    The threshold is the tenant config value ``brief_rules.alert_rules.min_score_for_qualified``
    read from ``core.tenant_config``, the same value ``services/core/app/qualification.py`` uses;
    when the tenant has no value the core model default applies, read from the model, not copied.
    """
    from services.core.app.tenant_config import AlertRules

    default = AlertRules().min_score_for_qualified
    sql = (
        "SELECT count(*) FROM core.company c "
        f"WHERE c.tenant_id = '{tenant_id}' AND c.score >= COALESCE(("
        "SELECT (t.brief_rules->'alert_rules'->>'min_score_for_qualified')::int "
        f"FROM core.tenant_config t WHERE t.tenant_id = c.tenant_id), {int(default)})"
    )
    if missing_lead:
        sql += (
            " AND NOT EXISTS (SELECT 1 FROM core.lead l WHERE l.company_id = c.id "
            "AND l.source = 'auto_qualification')"
        )
    return sql


def auto_leads_sql(tenant_id: str) -> str:
    """SQL counting the auto qualification leads of one tenant."""
    return (
        "SELECT count(*) FROM core.lead "
        f"WHERE tenant_id = '{tenant_id}' AND source = 'auto_qualification'"
    )


def compose_text(sql: str) -> str:
    """Run one SQL query inside the postgres container and return its unaligned output."""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c", PSQL, sql],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def compose_query(sql: str) -> int:
    """Run one scalar SQL query inside the postgres container and return the integer."""
    return int(compose_text(sql))


def dead_letter_errors() -> list[str]:
    """The most common dead letter errors with their counts (error texts hold no credentials)."""
    return [line for line in compose_text(DEAD_LETTER_ERRORS).splitlines() if line]


def backfill_in_ml(tenant_id: str, limit: int) -> None:
    """Run the feature backfill inside the ml container; raises when it exits non-zero."""
    result = subprocess.run(
        [*BACKFILL_COMMAND, "--tenant", tenant_id, "--limit", str(limit)],
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        raise RuntimeError(f"backfill failed: {result.stderr.strip()[-300:]}")


async def drive_chain(client: httpx.AsyncClient, secret: str) -> dict[str, str]:
    """Create one signal and move one lead to the next stage through core's internal API.

    Runs inside the gateway container. The signal uses the ``seed`` service identity and the lead
    calls use the ``gateway`` one, as in the allow lists of ``services/core/app/main.py``. Raises
    ``RuntimeError`` when core refuses a step. Returns the ids it created (no secrets).
    """
    from libs.common.internal_auth import GATEWAY_SERVICE, service_token_header
    from ops.seed.loader import DEMO_TENANT_ID

    def fail(step: str, response: httpx.Response) -> RuntimeError:
        return RuntimeError(f"{step} answered {response.status_code}")

    seed = service_token_header("seed", secret)
    gateway = {
        **service_token_header(GATEWAY_SERVICE, secret),
        "X-Tenant-Id": str(DEMO_TENANT_ID),
    }
    listed = await client.get("/internal/core/companies", params={"limit": 1}, headers=seed)
    if listed.status_code != 200 or not listed.json().get("data"):
        raise fail("company list", listed)
    company_id = str(listed.json()["data"][0]["id"])
    token = uuid4().hex
    signal = await client.post(
        "/internal/core/signals",
        json={
            "company_id": company_id,
            "source": "demo",
            "signal_type": "e2e_chain",
            "observed_at": datetime.now(UTC).isoformat(),
            "strength": 0.5,
            "source_external_id": f"e2e-chain-{token}",
            "payload": {"origin": "e2e_check"},
        },
        headers=seed,
    )
    if signal.status_code != 201:
        raise fail("signal create", signal)
    lead = await client.post(
        "/internal/core/leads",
        json={
            "company_id": company_id,
            "service_line": "e2e_chain",
            "qualification_reason": {"origin": "e2e_check"},
        },
        headers={**gateway, "Idempotency-Key": f"e2e-chain-{token}"},
    )
    if lead.status_code not in (200, 201):
        raise fail("lead create", lead)
    created = lead.json()
    changed = await client.patch(
        f"/internal/core/leads/{created['lead_id']}/status",
        json={"stage": "qualified", "expected_version": created["version"]},
        headers=gateway,
    )
    if changed.status_code != 200:
        raise fail("lead status change", changed)
    return {"company_id": company_id, "signal_id": str(signal.json()["signal_id"])}


def drive_in_gateway() -> None:
    """Run ``--drive-chain`` inside the gateway container; raises when it exits non-zero."""
    result = subprocess.run(
        [*DRIVE_COMMAND, "--drive-chain"], capture_output=True, text=True, check=False
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        raise RuntimeError(f"chain driver failed: {result.stderr.strip()[-300:]}")


async def _drive_main() -> int:
    """Entry of ``--drive-chain``: use the container settings and print one status line."""
    from libs.common.settings import get_settings

    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.core_url.rstrip("/"), timeout=15.0) as client:
        try:
            await drive_chain(client, settings.internal_service_token)
        except (RuntimeError, httpx.HTTPError) as error:
            print(f"chain driver: FAILED ({type(error).__name__}: {error})", file=sys.stderr)
            return 1
    print("chain driver: one signal and one lead status change sent")
    return 0


def chain_failures(published: int, unpublished: int, dead: int) -> list[str]:
    """Reasons the chain events do not meet the requirements of ``--chain``."""
    problems = []
    if published < len(CHAIN_TYPES):
        problems.append(f"only {published} of {len(CHAIN_TYPES)} chain event types are published")
    if unpublished:
        problems.append(f"{unpublished} chain events are still unpublished")
    if dead:
        problems.append(f"{dead} events went to the dead letter table")
    return problems


def check_chain(
    args: argparse.Namespace,
    query: Query,
    sleep: Callable[[float], None],
    drive: Callable[[], None],
) -> int:
    """Drive one signal and one status change, then poll the outbox for the chain event types."""
    try:
        drive()
    except (RuntimeError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    deadline = time.monotonic() + args.timeout
    while True:
        published = query(CHAIN_PUBLISHED)
        unpublished = query(CHAIN_UNPUBLISHED)
        dead = query(DEAD_LETTERS)
        problems = chain_failures(published, unpublished, dead)
        if not problems or time.monotonic() >= deadline:
            break
        sleep(args.interval)
    print(f"chain event types published: {published} of {len(CHAIN_TYPES)}")
    print(f"chain events unpublished: {unpublished}")
    print(f"outbox dead letters after chain: {dead}")
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    print("e2e chain check: " + ("FAILED" if problems else "OK"))
    return 1 if problems else 0


def check_leads_and_backfill(
    args: argparse.Namespace,
    query: Query,
    sleep: Callable[[float], None],
    backfill: Backfill,
) -> int:
    """Require auto qualification leads for qualified companies, then run the feature backfill.

    Polls (bounded by ``--timeout``) because the core worker creates leads after the scores land.
    Then runs the backfill for the demo tenant and requires no ml.lead_score row without features.
    """
    from ops.seed.loader import DEMO_TENANT_ID

    tenant = str(DEMO_TENANT_ID)
    all_sql = qualified_sql(tenant, missing_lead=False)
    missing_sql = qualified_sql(tenant, missing_lead=True)
    leads_sql = auto_leads_sql(tenant)
    deadline = time.monotonic() + args.timeout
    while True:
        qualified, missing, leads = query(all_sql), query(missing_sql), query(leads_sql)
        if not missing or time.monotonic() >= deadline:
            break
        sleep(args.interval)
    print(f"demo companies at the qualified threshold: {qualified}")
    print(f"core.lead rows from auto_qualification: {leads}")
    print(f"qualified companies without an auto_qualification lead: {missing}")
    problems = []
    if missing:
        problems.append(f"{missing} qualified companies have no auto_qualification lead")
    if not problems:
        try:
            backfill(tenant, args.backfill_limit)
        except (RuntimeError, OSError) as error:
            problems.append(str(error))
    if not problems:
        left = query(SCORES_WITHOUT_FEATURES)
        print(f"ml.lead_score without features after backfill: {left}")
        if left:
            problems.append(f"{left} ml.lead_score rows have no features after the backfill")
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    print("e2e leads and backfill check: " + ("FAILED" if problems else "OK"))
    return 1 if problems else 0


def collect(query: Query, tables: Sequence[Table]) -> dict[str, int]:
    """Row counts per table plus the two outbox counters."""
    counts = {table.name: query(f"SELECT count(*) FROM {table.name}") for table in tables}
    counts["outbox pending"] = query(PENDING_OUTBOX)
    counts["outbox dead letters"] = query(DEAD_LETTERS)
    counts["companies unscored"] = query(COMPANIES_UNSCORED)
    counts["scores without features"] = query(SCORES_WITHOUT_FEATURES)
    counts["scores with features"] = query(SCORES_WITH_FEATURES)
    return counts


def failures(counts: dict[str, int], tables: Sequence[Table], *, strict: bool) -> list[str]:
    """Human readable reasons the counts do not meet the requirements."""
    problems = [
        f"{table.name} is empty"
        for table in tables
        if (table.required or strict) and counts[table.name] == 0
    ]
    if counts["outbox pending"]:
        problems.append(f"{counts['outbox pending']} outbox events are still unpublished")
    if counts["outbox dead letters"]:
        problems.append(f"{counts['outbox dead letters']} events went to the dead letter table")
    if counts["companies unscored"]:
        problems.append(f"{counts['companies unscored']} core.company rows have no score")
    if counts["scores without features"]:
        problems.append(
            f"{counts['scores without features']} ml.lead_score rows have no stored features"
        )
    if counts["ml.lead_score"] and not counts["scores with features"]:
        problems.append("no ml.lead_score row has stored features")
    return problems


def run(
    argv: Sequence[str] | None = None,
    query: Query = compose_query,
    sleep: Callable[[float], None] = time.sleep,
    errors: DeadLetterErrors = dead_letter_errors,
    drive: Callable[[], None] = drive_in_gateway,
    backfill: Backfill | None = None,
) -> int:
    """Poll until the requirements hold or the timeout passes; print the final counts."""
    parser = argparse.ArgumentParser(description="Check demo data in the running stack.")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--strict", action="store_true", help="require every table, not only core")
    parser.add_argument(
        "--chain", action="store_true", help="then drive and check the event chain (flag on)"
    )
    parser.add_argument(
        "--backfill-limit", type=int, default=5, help="companies the backfill step rescores"
    )
    parser.add_argument("--drive-chain", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.drive_chain:
        return asyncio.run(_drive_main())

    deadline = time.monotonic() + args.timeout
    while True:
        counts = collect(query, TABLES)
        problems = failures(counts, TABLES, strict=args.strict)
        if not problems or time.monotonic() >= deadline:
            break
        sleep(args.interval)

    for table in TABLES:
        needed = "required" if table.required or args.strict else "info"
        print(f"{table.name}: {counts[table.name]} ({needed})")
    print(f"outbox pending: {counts['outbox pending']}")
    print(f"outbox dead letters: {counts['outbox dead letters']}")
    print(f"core.company without score: {counts['companies unscored']}")
    print(f"ml.lead_score with features: {counts['scores with features']}")
    print(f"ml.lead_score without features: {counts['scores without features']}")
    if counts["outbox dead letters"]:
        for line in errors():
            print(f"dead letter error: {line}")
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    print("e2e data check: " + ("FAILED" if problems else "OK"))
    if problems:
        return 1
    run_backfill = backfill if backfill is not None else backfill_in_ml
    if check_leads_and_backfill(args, query, sleep, run_backfill):
        return 1
    return check_chain(args, query, sleep, drive) if args.chain else 0


if __name__ == "__main__":
    raise SystemExit(run())
