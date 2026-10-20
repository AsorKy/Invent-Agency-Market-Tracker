"""RUES lookup queue worker (REQ-ING-003): retries, backoff, delivery through core."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx

from libs.domain.entity_resolution import MockRuesLookup, RuesRecord
from services.ingest.app.rues_queue import DUE_SQL, RuesQueueWorker
from services.ingest.app.to_core import HttpCoreClient, SnapshotFlow
from tests.unit.support_pipeline import FakeIngestEngine, _Rows

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
TENANT = UUID("00000000-0000-4000-8000-0000000000a1")


class QueueEngine(FakeIngestEngine):
    def dispatch(self, sql: str, params: dict[str, Any]) -> _Rows:
        if sql.startswith("SELECT q.candidate_id"):
            assert sql == " ".join(DUE_SQL.split())
            due = []
            for cid, queue in self.rues_queue.items():
                at = queue.get("next_attempt_at")
                if queue["status"] == "queued" and (at is None or at <= params["now"]):
                    candidate = next(c for c in self.candidates.values() if str(c["id"]) == cid)
                    due.append(
                        {
                            "candidate_id": candidate["id"],
                            "name": candidate["name"],
                            "city": candidate["city"],
                            "candidate_nit": candidate["candidate_nit"],
                            "resolution_attempts": candidate["resolution_attempts"],
                        }
                    )
            return _Rows(due)
        return super().dispatch(sql, params)


def add_candidate(
    engine: QueueEngine, *, attempts: int = 1, due: bool = True, nit: str | None = None
) -> str:
    cid = uuid4()
    engine.candidates[(uuid4(), "k")] = {
        "id": cid,
        "name": "Panaderia Sol",
        "city": "Cali",
        "candidate_nit": nit,
        "resolution_attempts": attempts,
        "status": "pending_rues",
    }
    engine.rues_queue[str(cid)] = {
        "status": "queued",
        "next_attempt_at": NOW - timedelta(minutes=1) if due else NOW + timedelta(hours=1),
    }
    return str(cid)


def build(engine: QueueEngine, rues: MockRuesLookup, status: int = 200) -> tuple[Any, list[Any]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"company_id": str(uuid4())})

    client = HttpCoreClient(
        "http://core",
        "tok",
        client=httpx.AsyncClient(base_url="http://core", transport=httpx.MockTransport(handler)),
    )
    flow = SnapshotFlow(
        core=client,
        failures=None,  # type: ignore[arg-type]
        resolve=lambda c: None,  # type: ignore[arg-type,return-value]
        dataset_sources={},
        tenant_id=TENANT,
        retry_attempts=1,
        retry_base_delay_seconds=0,
    )
    return RuesQueueWorker(engine=engine, rues=rues, flow=flow, now=lambda: NOW), calls  # type: ignore[arg-type]


async def test_resolved_candidate_goes_to_core_with_the_rues_nit() -> None:
    engine = QueueEngine()
    cid = add_candidate(engine, nit="900123456")
    rues = MockRuesLookup([RuesRecord("900123456", "Panaderia Sol", "Cali")])
    worker, calls = build(engine, rues)
    result = await worker.run_once()
    assert (result.resolved, result.delivered) == (1, 1)
    assert engine.rues_queue[cid]["status"] == "resolved"
    assert len(calls) == 1 and calls[0].headers["Idempotency-Key"]
    assert b'"nit":"900123456"' in calls[0].content.replace(b" ", b"")
    again = await worker.run_once()
    assert again.resolved == 0 and len(calls) == 1


async def test_unavailable_rues_is_requeued_with_backoff_then_quarantined() -> None:
    engine = QueueEngine()
    cid = add_candidate(engine, attempts=0)
    worker, calls = build(engine, MockRuesLookup(fail=True))
    first = await worker.run_once()
    assert first.retried == 1 and calls == []
    queued = engine.rues_queue[cid]
    assert queued["status"] == "queued" and queued["next_attempt_at"] > NOW
    assert (await worker.run_once()).retried == 0  # not due yet
    queued["next_attempt_at"] = NOW - timedelta(seconds=1)
    second = await worker.run_once()
    assert second.quarantined == 1
    assert engine.rues_queue[cid]["status"] == "quarantined"
    assert len(engine.quarantine) == 1 and calls == []


async def test_future_rows_are_left_alone() -> None:
    engine = QueueEngine()
    add_candidate(engine, due=False)
    worker, calls = build(engine, MockRuesLookup([]))
    result = await worker.run_once()
    assert result.resolved == result.retried == result.quarantined == 0 and calls == []


async def test_core_failure_requeues_the_resolved_candidate() -> None:
    engine = QueueEngine()
    cid = add_candidate(engine, nit="900123456")
    rues = MockRuesLookup([RuesRecord("900123456", "Panaderia Sol", "Cali")])
    worker, calls = build(engine, rues, status=503)
    result = await worker.run_once()
    assert result.delivery_failed == 1 and result.delivered == 0
    queued = engine.rues_queue[cid]
    assert queued["status"] == "queued" and queued["next_attempt_at"] > NOW
    assert "HTTPStatusError" in queued["last_error"]
