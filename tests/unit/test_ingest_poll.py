"""Unit tests for the polling fallback, trigger tracking, and the budget endpoint.

Covers REQ-ING-008 (each trigger is tracked in ``ingest.pending_snapshot`` and
polled until ready; a snapshot delivered by both webhook and polling is stored
once) and the ``GET /internal/ingest/budget/{source}`` route used by n8n
(200 while budget remains, 429 otherwise, internal token required). The
connector uses ``httpx.MockTransport``; repositories are in-memory fakes.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("yaml")

from services.ingest.app.budget import BudgetRepository  # noqa: E402
from services.ingest.app.connectors import BrightDataConnector  # noqa: E402
from services.ingest.app.poll import (  # noqa: E402
    PendingSnapshot,
    PendingSnapshotRepository,
    SnapshotPoller,
    trigger_and_track,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


class FakePending:
    """In-memory stand-in for ``PendingSnapshotRepository``."""

    def __init__(self, stored: set[str] | None = None) -> None:
        self.items: dict[UUID, dict[str, Any]] = {}
        self.stored = stored if stored is not None else set()

    def add(self, snapshot_id: str, source: str = "maps") -> UUID:
        key = uuid4()
        self.items[key] = {
            "source": source,
            "snapshot_id": snapshot_id,
            "attempts": 0,
            "status": "pending",
        }
        return key

    async def list_pending(self, limit: int = 50) -> list[PendingSnapshot]:
        return [
            PendingSnapshot(k, v["source"], v["snapshot_id"], v["attempts"])
            for k, v in self.items.items()
            if v["status"] == "pending"
        ]

    async def already_stored(self, snapshot_id: str) -> bool:
        return snapshot_id in self.stored

    async def set_status(self, pending_id: UUID, status: str) -> None:
        self.items[pending_id]["status"] = status

    async def record_error(self, pending_id: UUID, *, give_up: bool) -> None:
        item = self.items[pending_id]
        item["attempts"] += 1
        if give_up:
            item["status"] = "failed"

    async def track(self, source: str, snapshot_id: str, inputs: list[dict[str, Any]]) -> None:
        self.add(snapshot_id, source)


def connector(handler: Callable[[httpx.Request], httpx.Response]) -> BrightDataConnector:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://bd.test")
    return BrightDataConnector(
        "maps", client, dataset_id="gd_maps", api_token="t", retry_attempts=1
    )


def provider(status: str, records: list[dict[str, Any]] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/progress/" in request.url.path:
            return httpx.Response(200, json={"status": status, "records": len(records or [])})
        return httpx.Response(200, json=records or [])

    return handler


class Persist:
    """Record ``persist_snapshot`` calls like the webhook path does."""

    def __init__(self, pending: FakePending) -> None:
        self.calls: list[dict[str, Any]] = []
        self.pending = pending

    async def __call__(self, **kwargs: Any) -> tuple[str, bool, bool]:
        self.calls.append(kwargs)
        self.pending.stored.add(kwargs["snapshot_id"])
        return "row", True, True


def make_poller(
    handler: Any, pending: FakePending, **kwargs: Any
) -> tuple[SnapshotPoller, Persist]:
    persist = Persist(pending)
    poller = SnapshotPoller(
        pending,  # type: ignore[arg-type]
        {"maps": connector(handler)},
        persist,
        clock=lambda: NOW,
        **kwargs,
    )
    return poller, persist


async def test_ready_snapshot_is_stored_once_across_polls() -> None:
    pending = FakePending()
    key = pending.add("s1")
    records = [{"place_id": "p1", "name": "Cafe"}]
    poller, persist = make_poller(provider("ready", records), pending)
    assert (await poller.poll_once())["stored"] == 1
    assert (await poller.poll_once()) == {"stored": 0, "delivered": 0, "waiting": 0, "failed": 0}
    assert len(persist.calls) == 1
    call = persist.calls[0]
    assert call["snapshot_id"] == "s1" and call["dataset_id"] == "gd_maps"
    assert call["payload"] == records and call["fetched_at"] == NOW
    assert len(call["payload_sha256"]) == 64
    assert pending.items[key]["status"] == "stored"


async def test_snapshot_delivered_by_webhook_is_not_stored_again() -> None:
    pending = FakePending(stored={"s1"})
    key = pending.add("s1")
    poller, persist = make_poller(provider("ready", [{"a": 1}]), pending)
    assert (await poller.poll_once())["delivered"] == 1
    assert persist.calls == [] and pending.items[key]["status"] == "delivered"


async def test_webhook_arriving_during_download_wins() -> None:
    pending = FakePending()
    key = pending.add("s1")

    def handler(request: httpx.Request) -> httpx.Response:
        if "/snapshot/" in request.url.path:
            pending.stored.add("s1")  # webhook delivered while we download
        return provider("ready", [{"a": 1}])(request)

    poller, persist = make_poller(handler, pending)
    assert (await poller.poll_once())["delivered"] == 1
    assert persist.calls == [] and pending.items[key]["status"] == "delivered"


async def test_running_snapshot_keeps_waiting() -> None:
    pending = FakePending()
    key = pending.add("s1")
    poller, persist = make_poller(provider("running"), pending)
    assert (await poller.poll_once())["waiting"] == 1
    assert persist.calls == [] and pending.items[key]["status"] == "pending"


async def test_failed_snapshot_is_marked_failed() -> None:
    pending = FakePending()
    key = pending.add("s1")
    poller, _ = make_poller(provider("failed"), pending)
    assert (await poller.poll_once())["failed"] == 1
    assert pending.items[key]["status"] == "failed"


async def test_transient_errors_count_then_give_up() -> None:
    pending = FakePending()
    key = pending.add("s1")
    poller, _ = make_poller(lambda request: httpx.Response(503), pending, max_errors=2)
    assert (await poller.poll_once())["waiting"] == 1
    assert pending.items[key] == {**pending.items[key], "attempts": 1, "status": "pending"}
    assert (await poller.poll_once())["failed"] == 1
    assert pending.items[key]["status"] == "failed"


async def test_client_error_fails_immediately_and_unknown_source_fails() -> None:
    pending = FakePending()
    key = pending.add("s1")
    other = pending.add("s2", source="instagram")
    poller, _ = make_poller(lambda request: httpx.Response(401), pending)
    counts = await poller.poll_once()
    assert counts["failed"] == 2
    assert pending.items[key]["status"] == pending.items[other]["status"] == "failed"


async def test_trigger_and_track_records_snapshot_with_notify_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"snapshot_id": "s9"})

    pending = FakePending()
    snapshot_id = await trigger_and_track(
        connector(handler),
        pending,
        [{"k": 1}],
        notify_url="https://pub.test/hook",  # type: ignore[arg-type]
    )
    assert snapshot_id == "s9"
    assert seen[0].url.params["notify"] == "https://pub.test/hook"
    assert [v["snapshot_id"] for v in pending.items.values()] == ["s9"]


# --- SQL level: fake engine records statements ---------------------------------------------------


class SqlResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def mappings(self) -> "SqlResult":
        return self

    def all(self) -> list[Any]:
        return self.rows

    def scalar_one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None

    def first(self) -> Any:
        return self.rows[0] if self.rows else None


class SqlEngine:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def begin(self) -> "SqlEngine":
        return self

    def connect(self) -> "SqlEngine":
        return self

    async def __aenter__(self) -> "SqlEngine":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: Any, params: dict[str, Any]) -> SqlResult:
        self.calls.append((str(statement), params))
        return SqlResult(self.rows)


async def test_track_inserts_idempotently_with_input_hash() -> None:
    engine = SqlEngine([])
    await PendingSnapshotRepository(engine).track("maps", "s1", [{"k": 1}])
    sql, params = engine.calls[0]
    assert "INSERT INTO ingest.pending_snapshot" in sql and "DO NOTHING" in sql
    assert params["source"] == "maps" and json.loads(params["payload"]) == [{"k": 1}]
    assert len(params["payload_sha256"]) == 64


async def test_list_pending_and_already_stored_queries() -> None:
    row = {"id": uuid4(), "source": "maps", "snapshot_id": "s1", "attempts": 2}
    repo = PendingSnapshotRepository(SqlEngine([row]))
    items = await repo.list_pending()
    assert items == [PendingSnapshot(row["id"], "maps", "s1", 2)]
    assert await repo.already_stored("s1") is True
    assert await PendingSnapshotRepository(SqlEngine([])).already_stored("s1") is False


async def test_status_and_error_updates_run() -> None:
    engine = SqlEngine([])
    repo = PendingSnapshotRepository(engine)
    await repo.set_status(uuid4(), "stored")
    await repo.record_error(uuid4(), give_up=True)
    assert engine.calls[0][1]["status"] == "stored"
    assert engine.calls[1][1]["give_up"] is True


async def test_budget_remaining_is_zero_without_row_and_never_negative() -> None:
    assert await BudgetRepository(SqlEngine([])).remaining("maps", NOW) == 0
    assert await BudgetRepository(SqlEngine([-5])).remaining("maps", NOW) == 0
    assert await BudgetRepository(SqlEngine([40])).remaining("maps", NOW) == 40


# --- Budget and trigger endpoints --------------------------------------------------------------


class FakeBudget:
    def __init__(self, remaining: int) -> None:
        self.left = remaining

    async def remaining(self, source: str, now: datetime) -> int:
        return self.left


@pytest.fixture
def ingest_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    for module in ("pydantic_settings", "fastapi", "orjson", "structlog", "redis"):
        pytest.importorskip(module)
    from libs.common.settings import get_settings
    from services.ingest.app import main as ingest

    monkeypatch.setattr(get_settings(), "internal_service_token", "tok")
    yield ingest
    ingest.app.dependency_overrides.clear()


async def test_budget_endpoint_returns_200_then_429(
    ingest_app: Any, asgi_client: Callable[[Any], Any]
) -> None:
    budget = FakeBudget(10)
    ingest_app.app.dependency_overrides[ingest_app.get_budget_repository] = lambda: budget
    headers = {"X-Service-Token": "tok"}
    async with asgi_client(ingest_app.app) as client:
        ok = await client.get("/internal/ingest/budget/maps", headers=headers)
        budget.left = 0
        exhausted = await client.get("/internal/ingest/budget/maps", headers=headers)
    assert ok.status_code == 200 and ok.json() == {"source": "maps", "remaining": 10}
    assert exhausted.status_code == 429


async def test_budget_endpoint_requires_service_token(
    ingest_app: Any, asgi_client: Callable[[Any], Any]
) -> None:
    ingest_app.app.dependency_overrides[ingest_app.get_budget_repository] = lambda: FakeBudget(1)
    async with asgi_client(ingest_app.app) as client:
        missing = await client.get("/internal/ingest/budget/maps")
        wrong = await client.get("/internal/ingest/budget/maps", headers={"X-Service-Token": "x"})
    assert missing.status_code == wrong.status_code == 401
