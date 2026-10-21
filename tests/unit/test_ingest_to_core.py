"""Tests for the snapshot to core flow (REQ-ING-009) with fakes and no network."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from libs.common.internal_auth import verify_service_token
from libs.domain.entity_resolution import (
    EntityResolver,
    InMemoryResolutionStore,
    MockRuesLookup,
    Resolution,
    RuesRecord,
)
from services.ingest.app.to_core import (
    HttpCoreClient,
    Registered,
    SnapshotFlow,
    SqlResolutionStore,
)

NOW = datetime(2026, 10, 18, tzinfo=UTC)
TENANT = UUID("00000000-0000-4000-8000-0000000000a1")
MAPS_OK = {"place_id": "p1", "name": "Panaderia Sol", "city": "Cali", "url": "https://x.co"}
JOB_OK = {"job_id": "j1", "title": "Baker", "company": "Panaderia Sol", "city": "Cali"}


class FakeCore:
    def __init__(self, *, fail_signal_for: str | None = None, down: bool = False) -> None:
        self.companies: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.fail_signal_for = fail_signal_for
        self.down = down

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.down:
            raise httpx.ConnectError("down")
        self.companies.append(body)
        return {"company_id": str(uuid4()), "status": "created", "version": 1}

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]:
        if body["source_external_id"] == self.fail_signal_for:
            request = httpx.Request("POST", "http://core/x")
            raise httpx.HTTPStatusError(
                "bad", request=request, response=httpx.Response(422, request=request)
            )
        self.signals.append(body)
        return {"signal_id": str(uuid4()), "status": "created"}


class FakeFailures:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


def make_flow(
    core: FakeCore, failures: FakeFailures, rues: MockRuesLookup | None = None
) -> SnapshotFlow:
    store = InMemoryResolutionStore()
    resolver = EntityResolver(rues or MockRuesLookup(), store, now=NOW)
    return SnapshotFlow(
        core=core,
        failures=failures,
        resolve=resolver.resolve,
        dataset_sources={"d-maps": "maps", "d-jobs": "computrabajo"},
        tenant_id=TENANT,
        retry_attempts=1,
        retry_base_delay_seconds=0,
    )


async def register(name: str, city: str | None, nit: str | None) -> Registered:
    return Registered(id=str(uuid4()))


async def run(flow: SnapshotFlow, source: str, record: Any) -> Any:
    return await flow.process_record(
        source=source, snapshot_id="s1", record=record, register=register, observed_at=NOW
    )


async def test_company_record_upserts_company_then_signal() -> None:
    core, failures = FakeCore(), FakeFailures()
    outcome = await run(make_flow(core, failures), "maps", MAPS_OK)
    assert outcome.status == "delivered"
    assert core.companies[0]["legal_name"] == "Panaderia Sol"
    assert core.companies[0]["source_ids"] == {"maps": "p1"}
    assert core.companies[0]["tenant_id"] == str(TENANT)
    assert core.signals[0]["tenant_id"] == str(TENANT)
    signal = core.signals[0]
    assert signal["signal_type"] == "maps_listing"
    assert signal["source_external_id"] == "p1"
    assert signal["observed_at"] == NOW.isoformat()
    assert failures.rows == []


async def test_tenant_comes_from_configuration_not_from_the_payload() -> None:
    core, failures = FakeCore(), FakeFailures()
    record = MAPS_OK | {"tenant_id": str(uuid4())}
    await run(make_flow(core, failures), "maps", record)
    assert core.companies[0]["tenant_id"] == str(TENANT)
    assert core.signals[0]["tenant_id"] == str(TENANT)


async def test_job_posting_resolves_the_company_name() -> None:
    core, failures = FakeCore(), FakeFailures()
    outcome = await run(make_flow(core, failures), "computrabajo", JOB_OK)
    assert outcome.status == "delivered"
    assert core.companies[0]["legal_name"] == "Panaderia Sol"
    assert "source_ids" not in core.companies[0]
    assert core.signals[0]["signal_type"] == "job_posting"


async def test_matched_resolution_passes_the_rues_nit_to_core() -> None:
    core = FakeCore()
    flow = make_flow(core, FakeFailures())
    flow.resolve = lambda candidate: Resolution("matched", 1.0, "900123456")
    await run(flow, "maps", MAPS_OK)
    assert core.companies[0]["nit"] == "900123456"


async def test_name_only_rues_match_goes_to_review_without_core_call() -> None:
    rues = MockRuesLookup([RuesRecord("900123456", "Panaderia Sol", "Cali")])
    core, failures = FakeCore(), FakeFailures()
    outcome = await run(make_flow(core, failures, rues), "maps", MAPS_OK)
    assert outcome.status == "pending"
    assert core.companies == [] and failures.rows == []


async def test_mapping_failure_is_stored_and_does_not_block_the_batch() -> None:
    core, failures = FakeCore(), FakeFailures()
    flow = make_flow(core, failures)
    bad = {"place_id": "p0", "city": "Cali"}
    outcomes = [await run(flow, "maps", r) for r in (bad, "text", MAPS_OK)]
    assert [o.status for o in outcomes] == ["failed", "failed", "delivered"]
    assert len(failures.rows) == 2
    assert failures.rows[0]["record_ref"] == "p0"
    assert failures.rows[0]["reason"].startswith("mapping:")
    assert len(core.signals) == 1


async def test_core_rejection_is_stored_with_status_code() -> None:
    core, failures = FakeCore(fail_signal_for="p1"), FakeFailures()
    outcome = await run(make_flow(core, failures), "maps", MAPS_OK)
    assert outcome.status == "failed"
    assert "HTTP 422" in failures.rows[0]["reason"]


async def test_core_down_is_stored_per_record() -> None:
    core, failures = FakeCore(down=True), FakeFailures()
    flow = make_flow(core, failures)
    first = await run(flow, "maps", MAPS_OK)
    second = await run(flow, "maps", {**MAPS_OK, "place_id": "p2"})
    assert first.status == second.status == "failed"
    assert len(failures.rows) == 2
    assert "ConnectError" in failures.rows[0]["reason"]


async def test_skipped_candidate_makes_no_core_call() -> None:
    core, failures = FakeCore(), FakeFailures()

    async def parked(name: str, city: str | None, nit: str | None) -> Registered:
        return Registered(id="c1", status="manual_review")

    outcome = await make_flow(core, failures).process_record(
        source="maps", snapshot_id="s1", record=MAPS_OK, register=parked
    )
    assert outcome.status == "skipped"
    assert core.companies == [] and failures.rows == []


async def test_unavailable_rues_after_two_attempts_is_quarantined_as_failure() -> None:
    core, failures = FakeCore(), FakeFailures()
    flow = make_flow(core, failures, MockRuesLookup(fail=True))

    async def second_try(name: str, city: str | None, nit: str | None) -> Registered:
        return Registered(id="c1", attempts=1)

    first = await run(flow, "maps", MAPS_OK)
    second = await flow.process_record(
        source="maps", snapshot_id="s1", record=MAPS_OK, register=second_try
    )
    assert first.status == "pending"
    assert second.status == "failed"
    assert core.companies == []
    assert len(failures.rows) == 1


async def test_http_client_sends_service_token_and_routes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"company_id": str(uuid4())})

    client = HttpCoreClient(
        "http://core",
        "tok-test",
        client=httpx.AsyncClient(base_url="http://core", transport=httpx.MockTransport(handler)),
    )
    await client.upsert_company({"legal_name": "X"})
    await client.create_signal({"a": 1})
    await client.aclose()
    assert [r.url.path for r in seen] == [
        "/internal/core/companies/upsert",
        "/internal/core/signals",
    ]
    assert all(
        verify_service_token(r.headers["X-Service-Token"], "tok-test") == "ingest" for r in seen
    )
    assert json.loads(seen[0].content) == {"legal_name": "X"}


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.statements.append((str(statement), params))


async def test_sql_resolution_store_buffers_then_flushes_once() -> None:
    store = SqlResolutionStore()
    store.set_candidate_state("c1", "pending_rues", 1)
    store.enqueue_rues_lookup("c1", 1, NOW, None)
    store.quarantine("c1", "no match")
    connection = FakeConnection()
    assert connection.statements == []
    await store.flush(connection)  # type: ignore[arg-type]
    assert len(connection.statements) == 3
    assert "ON CONFLICT (candidate_id)" in connection.statements[1][0]
    await store.flush(connection)  # type: ignore[arg-type]
    assert len(connection.statements) == 3
