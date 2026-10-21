"""Ingest must not hold a database connection while it calls core (REQ-ING-009)."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from libs.domain.entity_resolution import EntityResolver, MockRuesLookup
from services.ingest.app.repository import IngestRepository
from services.ingest.app.to_core import HttpCoreClient, SnapshotFlow, SqlResolutionStore
from tests.unit.support_pipeline import FakeIngestEngine, _Connection

TENANT = UUID("00000000-0000-4000-8000-0000000000a1")
RECORD = {"place_id": "p1", "name": "Panaderia Sol", "city": "Cali", "url": "https://x.co"}


class CountingEngine(FakeIngestEngine):
    """Fake engine that counts the connections that are open right now."""

    def __init__(self) -> None:
        super().__init__()
        self.open = 0

    def begin(self) -> _Connection:
        return self._tracked()

    def connect(self) -> _Connection:
        return self._tracked()

    def _tracked(self) -> _Connection:
        engine = self

        class Tracked(_Connection):
            async def __aenter__(self) -> "Tracked":
                engine.open += 1
                return self

            async def __aexit__(self, *_exc: Any) -> None:
                engine.open -= 1

        return Tracked(self)


class FailureSink:
    def __init__(self, engine: CountingEngine) -> None:
        self.engine = engine

    async def record(self, **kwargs: Any) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                "INSERT INTO ingest.failed_record (x)",
                {**kwargs, "payload": json.dumps(kwargs["payload"])},
            )


def build(engine: CountingEngine, handler: Any) -> IngestRepository:
    store = SqlResolutionStore()
    rues = MockRuesLookup([])
    client = HttpCoreClient(
        "http://core",
        "tok",
        client=httpx.AsyncClient(base_url="http://core", transport=httpx.MockTransport(handler)),
    )
    repository = IngestRepository.__new__(IngestRepository)
    repository.engine = engine  # type: ignore[assignment]
    repository.resolution_store = store
    repository.flow = SnapshotFlow(
        core=client,
        failures=FailureSink(engine),
        resolve=lambda c: EntityResolver(rues, store, now=datetime.now(UTC)).resolve(c),
        dataset_sources={"d-maps": "maps"},
        tenant_id=TENANT,
        retry_attempts=1,
        retry_base_delay_seconds=0,
    )
    return repository


def add_snapshot(engine: CountingEngine, records: list[dict[str, Any]]) -> UUID:
    row_id = uuid4()
    engine.snapshots.append(
        {
            "id": row_id,
            "seq": 0,
            "snapshot_id": "s1",
            "dataset_id": "d-maps",
            "payload": records,
            "payload_sha256": "h",
        }
    )
    return row_id


async def test_no_connection_is_open_during_core_calls_and_writes_are_committed() -> None:
    engine = CountingEngine()
    seen: list[tuple[int, int, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((engine.open, len(engine.candidates), request.headers.get("Idempotency-Key")))
        if request.url.path.endswith("upsert"):
            return httpx.Response(200, json={"company_id": str(uuid4()), "status": "created"})
        return httpx.Response(201, json={"signal_id": str(uuid4())})

    repository = build(engine, handler)
    row_id = add_snapshot(engine, [RECORD])
    assert await repository.process_snapshot(row_id) == 1
    assert len(seen) == 2
    assert all(opened == 0 for opened, _, _ in seen)
    assert all(candidates == 1 for _, candidates, _ in seen)
    keys = [key for _, _, key in seen]
    assert all(keys) and keys[0] != keys[1]
    assert engine.open == 0


async def test_idempotency_keys_are_stable_across_runs() -> None:
    keys: list[list[str]] = []
    for _ in range(2):
        engine = CountingEngine()
        run: list[str] = []

        def handler(request: httpx.Request, run: list[str] = run) -> httpx.Response:
            run.append(request.headers["Idempotency-Key"])
            return httpx.Response(200, json={"company_id": str(uuid4())})

        repository = build(engine, handler)
        await repository.process_snapshot(add_snapshot(engine, [RECORD]))
        keys.append(run)
    assert keys[0] == keys[1]


async def test_core_failure_is_recorded_in_a_new_transaction() -> None:
    engine = CountingEngine()
    opened_at_failure: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        opened_at_failure.append(engine.open)
        return httpx.Response(500, json={})

    repository = build(engine, handler)
    assert await repository.process_snapshot(add_snapshot(engine, [RECORD])) == 0
    assert opened_at_failure == [0]
    assert len(engine.failed_records) == 1
    assert "HTTP 500" in engine.failed_records[0]["reason"]
    assert engine.open == 0
