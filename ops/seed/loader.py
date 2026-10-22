"""Feed datagen snapshots through the ingest flow and remove them again (REQ-RUN-003).

``load`` builds the deterministic datagen dataset, turns it into provider-shaped
``maps`` and ``computrabajo`` snapshot records, and runs every record through
``services.ingest.app.to_core.SnapshotFlow`` (mapping, mock RUES resolution,
delivery). Delivery goes through ``DemoCore``, a ``CoreClient`` decorator over
core's internal API. It tags every company with ``source_ids={"demo": "demo"}``
and every signal with ``source="demo"`` (original source kept in the payload),
so core's outbox, consumers, scores, rollups, and briefs fill downstream.

``clear`` never writes into another service's schema. It replays the same flow
against a recorder to compute the deterministic company slugs, lists companies
through core's API, and removes the matching ones with core's opt-out
operation, which also removes their signals. Both commands are idempotent:
company upserts and signal inserts are idempotent in core.

The core port is injectable (``SeedCore``) so tests run with an in-memory fake;
``HttpSeedCore`` is the real adapter. It relates to ``ops/seed/__main__.py``,
``datagen/builder.py``, and ``services/core/app``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from datagen.builder import build_dataset, build_snapshots
from libs.domain.entity_resolution import EntityResolver, InMemoryResolutionStore, MockRuesLookup
from ops.seed.enrich import CompanyProfile, TrackingCore
from services.core.app.companies import CompanyUpsert, company_slug
from services.ingest.app.to_core import HttpCoreClient, Registered, SnapshotFlow

DEMO_SOURCE = "demo"
DEMO_TENANT_ID = UUID("00000000-0000-4000-8000-0000000000a1")
DEFAULT_SEED = 42
DEFAULT_ORGS = 60
DEFAULT_DAYS = 180
DEFAULT_AS_OF = date(2026, 1, 1)
SNAPSHOT_SOURCES = ("maps", "computrabajo")
PAGE_SIZE = 200
MAX_PAGES = 1000


class SeedCore(Protocol):
    """Core's internal API as used by the seed; every call must be idempotent."""

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]: ...

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]: ...

    async def list_companies(
        self, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]: ...

    async def opt_out(self, company_id: str) -> dict[str, Any]: ...


class HttpSeedCore(HttpCoreClient):
    """Real ``SeedCore`` over core's internal HTTP API with the service token."""

    service_name = "seed"

    async def list_companies(
        self, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = await self._client.get(
            "/internal/core/companies", params=params, headers=self._auth_headers()
        )
        response.raise_for_status()
        body = response.json()
        return list(body["data"]), body.get("next_cursor")

    async def opt_out(self, company_id: str) -> dict[str, Any]:
        return await self._post(
            "/internal/core/compliance/opt-out",
            {"company_id": company_id, "requested_via": "demo_seed_clear"},
        )


def _demo_city(value: Any) -> Any:
    """Drop a ", Colombia" suffix so job and map records share one company."""
    return value.split(",")[0].strip() if isinstance(value, str) else value


def demo_company_body(body: dict[str, Any]) -> dict[str, Any]:
    """Return an upsert body tagged as demo with a normalized city."""
    tagged = dict(body)
    tagged["source_ids"] = {DEMO_SOURCE: DEMO_SOURCE}
    if "city" in tagged:
        tagged["city"] = _demo_city(tagged["city"])
    return tagged


def demo_signal_body(body: dict[str, Any]) -> dict[str, Any]:
    """Return a signal body with ``source=demo`` and a unique external id."""
    origin = str(body["source"])
    tagged = dict(body)
    tagged["source"] = DEMO_SOURCE
    tagged["source_external_id"] = f"{origin}:{body['source_external_id']}"
    tagged["payload"] = {**body.get("payload", {}), "origin_source": origin}
    return tagged


class DemoCore:
    """``CoreClient`` decorator that tags everything it forwards as demo."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await self.inner.upsert_company(demo_company_body(body))
        return result

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await self.inner.create_signal(demo_signal_body(body))
        return result


class RecordingCore:
    """Offline ``CoreClient`` that records upsert bodies and writes nothing."""

    def __init__(self) -> None:
        self.companies: list[dict[str, Any]] = []

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]:
        self.companies.append(body)
        slug = company_slug(CompanyUpsert(**body))
        return {"company_id": str(uuid5(NAMESPACE_URL, slug)), "status": "dry_run"}

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"signal_id": str(uuid5(NAMESPACE_URL, str(body["source_external_id"])))}


class NullFailures:
    """Failure sink that keeps failed records in memory instead of a database."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


@dataclass
class SeedResult:
    """Counts of one seed run."""

    delivered: int = 0
    skipped: int = 0
    failed: int = 0
    removed: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    profiles: dict[str, CompanyProfile] = field(default_factory=dict)


async def _register(name: str, city: str | None, nit: str | None) -> Registered:
    key = f"demo-candidate:{name}:{city}:{nit}"
    return Registered(id=str(uuid5(NAMESPACE_URL, key)))


def build_records(
    *, orgs: int, days: int, seed: int, as_of: date
) -> dict[str, list[dict[str, object]]]:
    """Return the provider-shaped snapshot records of the demo dataset."""
    dataset = build_dataset(city="all", orgs=orgs, days=days, seed=seed, as_of=as_of)
    return build_snapshots(dataset, seed=seed)


def _flow(core: Any, failures: NullFailures, now: datetime) -> SnapshotFlow:
    # An empty mock RUES lookup resolves every candidate as a new company.
    resolver = EntityResolver(MockRuesLookup([]), InMemoryResolutionStore(), now=now)
    return SnapshotFlow(
        core=core,
        failures=failures,
        resolve=resolver.resolve,
        dataset_sources={},
        tenant_id=DEMO_TENANT_ID,
        retry_attempts=2,
        retry_base_delay_seconds=0.1,
    )


async def _run_flow(
    core: Any, records: dict[str, list[dict[str, object]]], now: datetime
) -> tuple[SeedResult, NullFailures]:
    failures = NullFailures()
    flow = _flow(core, failures, now)
    result = SeedResult()
    for source in SNAPSHOT_SOURCES:
        for record in records[source]:
            outcome = await flow.process_record(
                source=source,
                snapshot_id=f"demo-{source}",
                record=record,
                register=_register,
                observed_at=_observed_at(record, now),
            )
            if outcome.status == "delivered":
                result.delivered += 1
            elif outcome.status == "failed":
                result.failed += 1
            else:
                result.skipped += 1
    for row in failures.rows:
        reason = str(row.get("reason") or "unknown")
        result.reasons[reason] = result.reasons.get(reason, 0) + 1
    return result, failures


def _observed_at(record: dict[str, object], default: datetime) -> datetime:
    raw = record.get("date_posted")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return default
    return default


def _anchor(as_of: date) -> datetime:
    return datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)


async def load(
    core: SeedCore,
    *,
    orgs: int = DEFAULT_ORGS,
    days: int = DEFAULT_DAYS,
    seed: int = DEFAULT_SEED,
    as_of: date = DEFAULT_AS_OF,
) -> SeedResult:
    """Feed the demo snapshots to core through the ingest flow; safe to repeat."""
    records = build_records(orgs=orgs, days=days, seed=seed, as_of=as_of)
    tracking = TrackingCore(DemoCore(core))
    result, _ = await _run_flow(tracking, records, _anchor(as_of))
    result.profiles = tracking.profiles
    return result


async def demo_slugs(
    *,
    orgs: int = DEFAULT_ORGS,
    days: int = DEFAULT_DAYS,
    seed: int = DEFAULT_SEED,
    as_of: date = DEFAULT_AS_OF,
) -> set[str]:
    """Return the company slugs the demo load produces, without any write."""
    records = build_records(orgs=orgs, days=days, seed=seed, as_of=as_of)
    recorder = RecordingCore()
    await _run_flow(DemoCore(recorder), records, _anchor(as_of))
    return {company_slug(CompanyUpsert(**body)) for body in recorder.companies}


async def clear(
    core: SeedCore,
    *,
    orgs: int = DEFAULT_ORGS,
    days: int = DEFAULT_DAYS,
    seed: int = DEFAULT_SEED,
    as_of: date = DEFAULT_AS_OF,
    page_size: int = PAGE_SIZE,
    on_progress: Callable[[int], None] | None = None,
) -> SeedResult:
    """Remove the demo companies (and their signals) through core's opt-out API.

    Pass the same ``orgs``, ``days``, ``seed`` and ``as_of`` used by ``load``.
    """
    slugs = await demo_slugs(orgs=orgs, days=days, seed=seed, as_of=as_of)
    found: list[str] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        rows, cursor = await core.list_companies(page_size, cursor)
        found.extend(str(row["id"]) for row in rows if row.get("slug") in slugs)
        if cursor is None:
            break
    result = SeedResult()
    for company_id in found:
        outcome = await core.opt_out(company_id)
        if outcome.get("status") == "removed":
            result.removed += 1
            if on_progress:
                on_progress(result.removed)
    return result


def http_core(base_url: str, service_token: str, timeout_seconds: float = 10.0) -> HttpSeedCore:
    """Build the real core adapter."""
    return HttpSeedCore(base_url, service_token, timeout_seconds=timeout_seconds, client=None)


__all__ = ["DEMO_SOURCE", "SeedCore", "SeedResult", "clear", "demo_slugs", "http_core", "load"]
