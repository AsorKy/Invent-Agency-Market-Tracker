"""Run one bounded live collection and report pass or fail per hop.

``make verify-live SOURCE=<name>`` (REQ-RUN-006) refuses to run unless
``INGEST_MODE=live``. It triggers one collection of at most 5 inputs for the
source through the ingest service, then follows the data through the hops
``trigger``, ``snapshot``, ``normalize``, ``resolve``, ``signal``, ``score``, and
``api_read``, polling each hop until a timeout. The first failing hop stops the
run, later hops are reported as skipped, and each failure prints the next steps.
After every hop passes it writes the snapshot records to a temporary directory
and runs ``ops/verify_mappings.py --capture`` (T-096, decision D-13), which
stores redacted fixtures and prints the mapping coverage of the source.

Inputs come from ``--inputs FILE`` (a JSON list of provider inputs). Service
URLs come from ``VERIFY_LIVE_INGEST_URL`` and ``VERIFY_LIVE_CORE_URL``; the
database is read through ``DATABASE_URL`` or the ``POSTGRES_*`` values. Values
of secrets are never printed. It relates to ``ops/doctor.py``,
``services/ingest/app/``, and ``libs/common/settings.py``. Exit code 0 means
every hop passed, 1 a hop failed, 2 a refusal or a usage error.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from libs.common.internal_auth import service_token_header  # noqa: E402
from ops import doctor, verify_mappings  # noqa: E402

MAX_RECORDS = 5
HOPS = ("trigger", "snapshot", "normalize", "resolve", "signal", "score", "api_read")
SOURCES = ("maps", "computrabajo", "linkedin_companies", "linkedin_jobs", "instagram")
NEXT_STEPS = {
    "trigger": "Run `make doctor`; check the dataset ID, API token, and budget of the source.",
    "snapshot": "Check PUBLIC_WEBHOOK_BASE_URL or INGEST_POLL_ENABLED and the ingest logs.",
    "normalize": "Run ops/verify_mappings.py on a sample; the mapping may not match the records.",
    "resolve": "Check ingest.failed_record and the ingest-processor logs.",
    "signal": "Check the ingest to core delivery (INTERNAL_SERVICE_TOKEN, core logs).",
    "score": "Check the ml service logs and that the scoring rules exist.",
    "api_read": "Check CORE_URL, the gateway, and INTERNAL_SERVICE_TOKEN.",
}


class Backend(Protocol):
    """What the verification needs from the running stack."""

    def trigger(self, source: str, inputs: list[dict[str, Any]]) -> str:
        """Start a collection and return the snapshot ID."""

    def count(self, hop: str, source: str, snapshot_id: str, since: datetime) -> int:
        """Return how many rows reached ``hop`` for this run."""

    def records(self, snapshot_id: str) -> list[dict[str, Any]]:
        """Return the provider records stored in the snapshot."""

    def api_read(self, source: str) -> int:
        """Return how many leads the API read returns."""


@dataclass
class HopResult:
    """Outcome of one hop."""

    hop: str
    status: str  # pass, fail, or skipped
    detail: str = ""


class StackError(Exception):
    """A call to the running stack failed; the message holds no secret."""


def refusal(values: Mapping[str, str], source: str) -> str | None:
    """Return why the run must not start, or None when it may."""
    if (values.get("INGEST_MODE", "mock").strip() or "mock") != "live":
        return "refused: INGEST_MODE is not live (mock mode); verify-live needs live sources"
    if source not in SOURCES:
        return "refused: unknown source; use one of " + ", ".join(SOURCES)
    prefix = f"SOURCE_{source.upper()}"
    if values.get(f"{prefix}_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return f"refused: {prefix}_ENABLED is not true"
    missing = [
        name
        for name in (f"{prefix}_DATASET_ID", "BRIGHT_DATA_API_TOKEN", "INTERNAL_SERVICE_TOKEN")
        if not values.get(name, "").strip()
    ]
    if missing:
        return "refused: missing " + ", ".join(missing) + " (names only; see `make doctor`)"
    return None


def load_inputs(path: Path) -> list[dict[str, Any]]:
    """Read the provider inputs and cap them to ``MAX_RECORDS``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data or not all(isinstance(i, dict) for i in data):
        raise ValueError("inputs file must hold a non-empty JSON list of objects")
    return list(data[:MAX_RECORDS])


def run_hops(
    backend: Backend,
    source: str,
    inputs: list[dict[str, Any]],
    *,
    timeout_seconds: float = 300.0,
    interval_seconds: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[list[HopResult], str | None]:
    """Run every hop in order; return the results and the snapshot ID."""
    results: list[HopResult] = []
    snapshot_id: str | None = None
    since = now()
    try:
        snapshot_id = backend.trigger(source, inputs[:MAX_RECORDS])
        results.append(HopResult("trigger", "pass", "collection triggered"))
    except StackError as error:
        results.append(HopResult("trigger", "fail", str(error)))
    for hop in HOPS[1:]:
        if results[-1].status != "pass" or snapshot_id is None:
            results.append(HopResult(hop, "skipped"))
            continue
        deadline = clock() + timeout_seconds
        while True:
            try:
                found = (
                    backend.api_read(source)
                    if hop == "api_read"
                    else backend.count(hop, source, snapshot_id, since)
                )
                error_text = ""
            except StackError as error:
                found, error_text = 0, str(error)
            if found > 0:
                results.append(HopResult(hop, "pass", f"{found} found"))
                break
            if clock() >= deadline:
                results.append(HopResult(hop, "fail", error_text or "nothing found before timeout"))
                break
            sleep(interval_seconds)
    return results, snapshot_id


def format_report(source: str, results: Sequence[HopResult]) -> list[str]:
    """Return the report lines, with next steps for the first failure."""
    lines = [f"verify-live source={source}"]
    for item in results:
        suffix = f" ({item.detail})" if item.detail else ""
        lines.append(f"  {item.hop}: {item.status.upper()}{suffix}")
    failed = next((item for item in results if item.status == "fail"), None)
    if failed is None:
        lines.append("Result: PASS")
    else:
        lines += ["Result: FAIL", f"Next step: {NEXT_STEPS[failed.hop]}"]
    return lines


def capture_mappings(source: str, records: list[dict[str, Any]]) -> int:
    """Store redacted records and print the coverage through ``verify_mappings``."""
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / f"{source}.json").write_text(json.dumps(records), encoding="utf-8")
        return verify_mappings.main(["--samples", directory, "--source", source, "--capture"])


class LiveBackend:
    """Backend that calls the running stack over HTTP and reads Postgres."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._token = values.get("INTERNAL_SERVICE_TOKEN", "")
        self._ingest = values.get("VERIFY_LIVE_INGEST_URL") or "http://localhost:8001"
        self._core = values.get("VERIFY_LIVE_CORE_URL") or "http://localhost:8002"
        user = values.get("POSTGRES_USER") or "postgres"
        password = values.get("POSTGRES_PASSWORD") or ""
        database = values.get("POSTGRES_DB") or "invent_market_tracker"
        self._database = (
            values.get("VERIFY_LIVE_DATABASE_URL")
            or f"postgresql+asyncpg://{user}:{password}@localhost:5432/{database}"
        )

    def _http(self, method: str, url: str, body: Any = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(  # noqa: S310
            url,
            data=data,
            method=method,
            headers=service_token_header("verify_live", self._token),
        )
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return json.loads(response.read(1_048_576).decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise StackError(f"HTTP {error.code} from {method} {url.split('?')[0]}") from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise StackError(f"unreachable or invalid answer from {url.split('?')[0]}") from error

    def trigger(self, source: str, inputs: list[dict[str, Any]]) -> str:
        body = self._http("POST", f"{self._ingest}/internal/ingest/trigger/{source}", inputs)
        snapshot_id = body.get("snapshot_id") if isinstance(body, dict) else None
        if not snapshot_id:
            raise StackError("trigger answered without a snapshot_id")
        return str(snapshot_id)

    def _query(self, sql: str, params: dict[str, Any]) -> list[Any]:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        async def go() -> list[Any]:
            engine = create_async_engine(self._database)
            try:
                async with engine.connect() as connection:
                    return list((await connection.execute(text(sql), params)).all())
            finally:
                await engine.dispose()

        try:
            return asyncio.run(go())
        except Exception as error:  # noqa: BLE001
            raise StackError(f"database query failed ({type(error).__name__})") from error

    def count(self, hop: str, source: str, snapshot_id: str, since: datetime) -> int:
        queries = {
            "snapshot": "SELECT count(*) FROM ingest.raw_snapshot WHERE snapshot_id = :sid",
            "normalize": (
                "SELECT count(*) FROM ingest.entity_candidate c JOIN ingest.raw_snapshot r "
                "ON r.id = c.raw_snapshot_id WHERE r.snapshot_id = :sid"
            ),
            "resolve": (
                "SELECT count(*) FROM ingest.entity_candidate c JOIN ingest.raw_snapshot r "
                "ON r.id = c.raw_snapshot_id WHERE r.snapshot_id = :sid AND c.status <> 'pending'"
            ),
            "signal": (
                "SELECT count(*) FROM core.signal WHERE source = :source AND created_at >= :since"
            ),
            "score": "SELECT count(*) FROM ml.lead_score WHERE as_of >= :since",
        }
        rows = self._query(queries[hop], {"sid": snapshot_id, "source": source, "since": since})
        return int(rows[0][0]) if rows else 0

    def records(self, snapshot_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT payload FROM ingest.raw_snapshot WHERE snapshot_id = :sid", {"sid": snapshot_id}
        )
        found: list[dict[str, Any]] = []
        for (payload,) in rows:
            data = json.loads(payload) if isinstance(payload, str) else payload
            if isinstance(data, dict):
                data = data.get("records", data.get("payload", []))
            found += [item for item in data if isinstance(item, dict)]
        return found[:MAX_RECORDS]

    def api_read(self, source: str) -> int:
        del source
        body = self._http("GET", f"{self._core}/internal/core/leads?limit={MAX_RECORDS}")
        data = body.get("data") if isinstance(body, dict) else None
        return len(data) if isinstance(data, list) else 0


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    backend: Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the verification; ``backend`` is injectable for tests."""
    parser = argparse.ArgumentParser(description="Verify one live source end to end.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--inputs", type=Path, help="JSON list of provider inputs")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds per hop")
    args = parser.parse_args(argv)
    values = doctor.collect_values(ROOT / ".env", os.environ if environ is None else environ)
    reason = refusal(values, args.source)
    if reason:
        print(reason)
        return 2
    if args.inputs is None:
        print("usage error: --inputs FILE is required (JSON list of provider inputs, max 5 used)")
        return 2
    try:
        inputs = load_inputs(args.inputs)
    except (OSError, ValueError) as error:
        print(f"usage error: {error}")
        return 2
    active = backend or LiveBackend(values)
    results, snapshot_id = run_hops(
        active, args.source, inputs, timeout_seconds=args.timeout, sleep=sleep
    )
    print("\n".join(format_report(args.source, results)))
    if any(item.status != "pass" for item in results) or snapshot_id is None:
        return 1
    try:
        records = active.records(snapshot_id)
    except StackError as error:
        print(f"capture skipped: {error}")
        return 1
    return capture_mappings(args.source, records)


if __name__ == "__main__":
    raise SystemExit(main())
