"""Coverage lift for service modules that need SQL, Redis loops or entry points (REQ-QA-003).

Each test replaces the engine, Redis and settings with the fakes of
``support_cov_services.py`` and ``support_outbox.py``, so nothing connects to Postgres or
Redis. The tests assert the SQL parameters and the control flow (acknowledgement, retries,
status codes) of the ingest, ml, pulse and core modules listed in the run report.
"""

import asyncio
import json
import sys
import types
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import fakeredis
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("pydantic_settings")

from datagen.builder import build_dataset  # noqa: E402
from libs.common.internal_auth import sign_service_token  # noqa: E402
from libs.common.settings import get_settings  # noqa: E402
from services.core.app import dead_letter, republish  # noqa: E402
from services.ingest.app import main as ingest_main  # noqa: E402
from services.ingest.app import processor  # noqa: E402
from services.ingest.app import repository as ingest_repository  # noqa: E402
from services.ingest.app.budget import BudgetRepository  # noqa: E402
from services.ingest.app.connectors import ConnectorError  # noqa: E402
from services.ingest.app.to_core import RecordOutcome  # noqa: E402
from services.ml.app import main as ml_main  # noqa: E402
from services.ml.app import repository as ml_repository  # noqa: E402
from services.ml.app import segmentation as seg  # noqa: E402
from services.pulse.app import rollup_close  # noqa: E402
from tests.unit.support_cov_services import (  # noqa: E402
    AsyncExit,
    FakeConnection,
    FakeEngine,
    FakeRedisWrapper,
    FakeResult,
    Stop,
)
from tests.unit.support_outbox import make_settings  # noqa: E402

TENANT = UUID("11111111-1111-1111-1111-111111111111")
COMPANY = UUID("22222222-2222-2222-2222-222222222222")
ROW_ID = UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _headers() -> dict[str, str]:
    return {"X-Service-Token": sign_service_token("gateway", get_settings().internal_service_token)}


# --------------------------------------------------------------------------- ingest repository


def _ingest_repo(
    monkeypatch: pytest.MonkeyPatch, script: list[FakeResult] | None = None, *, engine: bool = True
) -> tuple[Any, FakeEngine | None, Any]:
    fake_engine = FakeEngine(script) if engine else None
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(ingest_repository, "create_engine", lambda *_a, **_k: fake_engine)
    monkeypatch.setattr(ingest_repository, "create_redis", lambda _url: redis)
    repo: Any = ingest_repository.IngestRepository("postgresql://x/y", "redis://x/0")
    return repo, fake_engine, redis


def test_ingest_repository_rejects_bad_ttl() -> None:
    with pytest.raises(ValueError, match="TTL"):
        ingest_repository.IngestRepository(None, "redis://x/0", enqueue_mark_ttl_seconds=0)


def test_candidate_key_is_deterministic_and_sensitive() -> None:
    a = ingest_repository.candidate_key("acme", "Cali", None)
    assert a == ingest_repository.candidate_key("acme", "Cali", None)
    assert a != ingest_repository.candidate_key("acme", "Cali", "900")


async def test_persist_snapshot_created_enqueues_once(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, engine, redis = _ingest_repo(monkeypatch, [FakeResult(scalar=ROW_ID)])
    args: dict[str, Any] = {
        "snapshot_id": "s1",
        "dataset_id": "d1",
        "payload": {"a": 1},
        "payload_sha256": "abc",
        "fetched_at": NOW,
    }
    assert await repo.persist_snapshot(**args) == (ROW_ID, True, True)
    assert engine is not None
    assert json.loads(engine.connection.calls[0][1]["payload"]) == {"a": 1}
    entries = await redis.xrange("invent.ingest")
    assert entries[0][1]["raw_snapshot_id"] == str(ROW_ID)
    # Same row again: the Redis mark suppresses a second enqueue.
    engine.connection.script = [FakeResult(scalar=None), FakeResult(scalar=ROW_ID)]
    assert await repo.persist_snapshot(**args) == (ROW_ID, False, False)
    assert len(engine.connection.calls) == 3
    assert len(await redis.xrange("invent.ingest")) == 1
    await repo.close()
    assert engine.disposed


async def test_persist_snapshot_requires_database(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _engine, _redis = _ingest_repo(monkeypatch, engine=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await repo.persist_snapshot(
            snapshot_id="s", dataset_id="d", payload=[], payload_sha256="h", fetched_at=NOW
        )
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await repo.process_snapshot(ROW_ID)
    await repo.close()


async def test_enqueue_failure_releases_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _engine, redis = _ingest_repo(monkeypatch)
    repo.redis = FakeRedisWrapper(redis, fail_xadd=True)
    with pytest.raises(ConnectionError):
        await repo._enqueue_snapshot(row_id=ROW_ID, dataset_id="d", snapshot_id="s")
    assert await redis.exists(f"invent:ingest:queued:{ROW_ID}") == 0


def _snapshot_script(records: list[Any], previous: list[Any] | None = None) -> list[FakeResult]:
    current = FakeResult([{"payload": records, "dataset_id": "d1", "snapshot_id": "s1"}])
    prior = FakeResult([{"payload": previous}] if previous is not None else [])
    return [current, prior]


async def test_process_snapshot_missing_row(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _engine, _redis = _ingest_repo(monkeypatch, [FakeResult()])
    with pytest.raises(ValueError, match="not found"):
        await repo.process_snapshot(ROW_ID)


async def test_process_snapshot_creates_candidates_for_valid_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: list[Any] = [
        {"name": "Acme SAS", "city": "Cali", "nit": "900"},
        {"business_name": "Beta", "city": 7},
        {"title": "Gamma"},
        {"name": "  "},
        {"other": "x"},
        "not-a-dict",
    ]
    script = [*_snapshot_script(records), *[FakeResult(rowcount=1) for _ in range(3)]]
    repo, engine, _redis = _ingest_repo(monkeypatch, script)
    assert await repo.process_snapshot(ROW_ID) == 3
    assert engine is not None
    inserts = [c for c in engine.connection.calls if "INSERT INTO ingest.entity_candidate" in c[0]]
    assert [c[1]["name"] for c in inserts] == ["Acme SAS", "Beta", "Gamma"]
    assert inserts[0][1]["candidate_nit"] == "900"
    assert inserts[1][1]["city"] is None


async def test_process_snapshot_skips_unchanged_records(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [{"name": "Acme", "id": "1"}]
    repo, engine, _redis = _ingest_repo(monkeypatch, _snapshot_script(records, previous=records))
    assert await repo.process_snapshot(ROW_ID) == 0
    assert engine is not None
    assert not any("INSERT" in c[0] for c in engine.connection.calls)


class _Flow:
    def __init__(self, source: str | None, status: str = "delivered") -> None:
        self.source = source
        self.status = status
        self.failures = SimpleNamespace(record=self._record)
        self.recorded: list[dict[str, Any]] = []
        self.registered: list[Any] = []

    async def _record(self, **kwargs: Any) -> None:
        self.recorded.append(kwargs)

    def source_for_dataset(self, dataset_id: str) -> str | None:
        return self.source

    async def prepare_record(
        self,
        *,
        source: str,
        snapshot_id: str,
        record: Any,
        register: Callable[..., Awaitable[Any]],
        observed_at: Any = None,
    ) -> Any:
        self.registered.append(await register(record["name"], "Cali", None))
        if record.get("bad"):
            return RecordOutcome("failed", "bad record")
        return record  # stands in for the prepared delivery

    async def deliver_prepared(self, prepared: Any) -> RecordOutcome:
        return RecordOutcome(self.status)


class _Store:
    def __init__(self) -> None:
        self.flushed = 0

    async def flush(self, connection: Any) -> None:
        self.flushed += 1


async def test_process_snapshot_delivers_records_through_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [{"name": "Acme"}, {"name": "Bad", "bad": True}]
    candidate = FakeResult([{"id": ROW_ID, "resolution_attempts": 2, "status": "new"}])
    script = [*_snapshot_script(records), candidate, candidate]
    repo, engine, _redis = _ingest_repo(monkeypatch, script)
    flow, store = _Flow("maps"), _Store()
    repo.flow, repo.resolution_store = flow, store
    assert await repo.process_snapshot(ROW_ID) == 1
    assert store.flushed == 2
    assert flow.registered[0].attempts == 2
    assert engine is not None
    assert any(
        "ON CONFLICT (raw_snapshot_id, candidate_key)" in c[0] for c in engine.connection.calls
    )


async def test_deliver_records_without_mapping_records_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _engine, _redis = _ingest_repo(monkeypatch, _snapshot_script([{"name": "A"}]))
    flow = _Flow(None)
    repo.flow, repo.resolution_store = flow, _Store()
    assert await repo.process_snapshot(ROW_ID) == 0
    assert flow.recorded[0]["source"] == "unknown"
    assert "d1" in flow.recorded[0]["reason"]
    repo.flow = None
    with pytest.raises(RuntimeError, match="not configured"):
        await repo._deliver_records(ROW_ID, "d1", "s1", [])


# --------------------------------------------------------------------------- ingest processor


def _processor_settings(**extra: Any) -> SimpleNamespace:
    base = make_settings()
    return SimpleNamespace(
        **vars(base),
        ingest_enqueue_mark_ttl_seconds=60,
        core_url="http://core",
        internal_service_token="t",
        upstream_timeout_seconds=1.0,
        upstream_retry_attempts=1,
        upstream_retry_base_delay_seconds=0.0,
        upstream_retry_max_delay_seconds=0.0,
        source_maps_dataset_id="ds-maps",
        enabled_sources=lambda: ["maps", "nodataset"],
        **extra,
    )


class _ProcRedis:
    def __init__(self) -> None:
        self.acked: list[str] = []

    async def xack(self, stream: str, group: str, message_id: str) -> None:
        self.acked.append(message_id)


class _ProcRepo:
    instances: list["_ProcRepo"] = []
    engine: Any = object()

    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.redis = _ProcRedis()
        self.processed: list[UUID] = []
        self.closed = False
        self.fail = False
        _ProcRepo.instances.append(self)

    async def process_snapshot(self, snapshot_id: UUID) -> int:
        if self.fail:
            raise RuntimeError("boom")
        self.processed.append(snapshot_id)
        return 1

    async def close(self) -> None:
        self.closed = True


class _ProcCore:
    closed = False

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def aclose(self) -> None:
        _ProcCore.closed = True


def _patch_processor(
    monkeypatch: pytest.MonkeyPatch, batches: list[Any], *, engine: Any = True
) -> None:
    _ProcRepo.instances = []
    _ProcRepo.engine = engine
    _ProcCore.closed = False
    monkeypatch.setattr(processor, "get_settings", _processor_settings)
    monkeypatch.setattr(processor, "IngestRepository", _ProcRepo)
    monkeypatch.setattr(processor, "HttpCoreClient", _ProcCore)
    monkeypatch.setattr(processor, "SqlFailureSink", lambda e: e)
    queue = list(batches)

    async def ensure(*_a: Any, **_k: Any) -> None:
        return None

    async def read(*_a: Any, **_k: Any) -> Any:
        if not queue:
            raise Stop()
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(processor, "ensure_consumer_group", ensure)
    monkeypatch.setattr(processor, "read_consumer_batch", read)


async def test_processor_acks_only_successful_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    good = str(uuid4())
    batch = [("invent.ingest", [("1-0", {"raw_snapshot_id": good}), ("2-0", {"x": "y"})])]
    _patch_processor(monkeypatch, [batch])
    with pytest.raises(Stop):
        await processor.main()
    repo = _ProcRepo.instances[0]
    assert repo.redis.acked == ["1-0"]
    assert repo.processed == [UUID(good)]
    assert repo.closed and _ProcCore.closed
    flow = repo.flow  # type: ignore[attr-defined]
    assert flow.dataset_sources == {"ds-maps": "maps"}
    assert repo.resolution_store is not None  # type: ignore[attr-defined]


async def test_processor_leaves_failed_message_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = [("invent.ingest", [("1-0", {"raw_snapshot_id": str(uuid4())})])]
    _patch_processor(monkeypatch, [batch], engine=None)
    original = _ProcRepo.__init__

    def failing(self: _ProcRepo, *a: Any, **k: Any) -> None:
        original(self, *a, **k)
        self.fail = True

    monkeypatch.setattr(_ProcRepo, "__init__", failing)
    with pytest.raises(Stop):
        await processor.main()
    repo = _ProcRepo.instances[0]
    assert repo.redis.acked == []
    assert not hasattr(repo, "flow")
    assert repo.closed


# --------------------------------------------------------------------------- ingest main


class _IngestRepoStub:
    engine = FakeEngine()

    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.error: Exception | None = None
        self.closed = False

    async def persist_snapshot(self, **kwargs: Any) -> tuple[UUID, bool, bool]:
        if self.error:
            raise self.error
        return ROW_ID, True, False

    async def close(self) -> None:
        self.closed = True


class _Budget:
    def __init__(self, remaining: int) -> None:
        self._remaining = remaining

    async def remaining(self, source: str, now: datetime) -> int:
        return self._remaining


class _Connector:
    def __init__(self, error: bool = False) -> None:
        self.error = error


@pytest.fixture
def ingest_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(ingest_main, "IngestRepository", _IngestRepoStub)
    monkeypatch.setattr(ingest_main, "build_connectors", lambda _s: {"maps": _Connector()})
    return ingest_main.app


async def test_ingest_lifespan_starts_and_cancels_poller(
    ingest_app: Any, asgi_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[float] = []

    class Poller:
        def __init__(self, *_a: Any) -> None:
            pass

        async def run_forever(self, interval: float) -> None:
            started.append(interval)
            await asyncio.sleep(3600)

    monkeypatch.setattr(ingest_main, "SnapshotPoller", Poller)
    monkeypatch.setattr(ingest_main.settings, "ingest_poll_enabled", True)
    async with asgi_client(ingest_app) as client:
        await asyncio.sleep(0)
        assert (await client.get("/health")).json()["service"] == "ingest"
        repo = ingest_app.state.repository
        assert isinstance(ingest_app.state.budget, BudgetRepository)
        assert ingest_app.state.pending is not None
    assert started
    assert repo.closed


async def test_ingest_ready_reports_dependencies(
    ingest_app: Any, asgi_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"database": True, "redis": True}

    async def db(_url: Any) -> bool:
        return state["database"]

    async def redis(_url: Any) -> bool:
        return state["redis"]

    monkeypatch.setattr(ingest_main, "check_database", db)
    monkeypatch.setattr(ingest_main, "check_redis", redis)
    async with asgi_client(ingest_app) as client:
        assert (await client.get("/ready")).status_code == 200
        state["redis"] = False
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"


async def test_ingest_webhook_maps_repository_errors(
    ingest_app: Any, asgi_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest_main, "valid_signed_webhook", lambda *_a, **_k: True)
    body = {
        "snapshot_id": "s1",
        "dataset_id": "d1",
        "payload": {"a": 1},
        "fetched_at": NOW.isoformat(),
    }
    async with asgi_client(ingest_app) as client:
        ok = await client.post("/internal/ingest/webhook/bd", json=body)
        assert ok.status_code == 202
        assert ok.json()["next_step"] == "already_queued"
        ingest_app.state.repository.error = RuntimeError("DATABASE_URL is required")
        assert (await client.post("/internal/ingest/webhook/bd", json=body)).status_code == 503
        monkeypatch.setattr(ingest_main, "valid_signed_webhook", lambda *_a, **_k: False)
        assert (await client.post("/internal/ingest/webhook/bd", json=body)).status_code == 401


async def test_ingest_process_endpoint_and_budget(ingest_app: Any, asgi_client: Any) -> None:
    async with asgi_client(ingest_app) as client:
        assert (await client.post("/internal/ingest/process/abc")).status_code == 401
        done = await client.post("/internal/ingest/process/abc", headers=_headers())
        assert done.json() == {"status": "queued", "snapshot_id": "abc"}
        ingest_app.state.budget = None
        missing = await client.get("/internal/ingest/budget/maps", headers=_headers())
        assert missing.status_code == 503
        ingest_app.state.budget = _Budget(5)
        ok = await client.get("/internal/ingest/budget/maps", headers=_headers())
        assert ok.json() == {"source": "maps", "remaining": 5}
        ingest_app.state.budget = _Budget(0)
        spent = await client.get("/internal/ingest/budget/maps", headers=_headers())
        assert spent.status_code == 429


async def test_ingest_trigger_endpoint_paths(
    ingest_app: Any, asgi_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes: list[Any] = ["snap-1", ConnectorError("down")]

    async def trigger(*_a: Any, **_k: Any) -> str:
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return str(result)

    monkeypatch.setattr(ingest_main, "trigger_and_track", trigger)
    url = "/internal/ingest/trigger/{}"
    async with asgi_client(ingest_app) as client:
        ingest_app.state.budget = _Budget(3)
        unknown = await client.post(url.format("nope"), json=[], headers=_headers())
        assert unknown.status_code == 404
        ok = await client.post(url.format("maps"), json=[{"q": 1}], headers=_headers())
        assert ok.json() == {"status": "triggered", "source": "maps", "snapshot_id": "snap-1"}
        failed = await client.post(url.format("maps"), json=[], headers=_headers())
        assert failed.status_code == 502
        ingest_app.state.budget = _Budget(0)
        spent = await client.post(url.format("maps"), json=[], headers=_headers())
        assert spent.status_code == 429


# --------------------------------------------------------------------------- ml repository


def _ml_repo(
    monkeypatch: pytest.MonkeyPatch, script: list[FakeResult] | None = None, *, engine: bool = True
) -> tuple[Any, FakeEngine | None]:
    fake = FakeEngine(script) if engine else None
    monkeypatch.setattr(ml_repository, "create_engine", lambda *_a, **_k: fake)
    return ml_repository.MlRepository("postgresql://x/y"), fake


async def test_ml_repository_writes_score_history_and_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, engine = _ml_repo(monkeypatch)
    assert engine is not None
    await repo.upsert_score(
        company_id=COMPANY, score=80, is_hot=True, source_version="v1", score_version="r:v1"
    )
    sql = [c[0] for c in engine.connection.calls]
    assert "ml.lead_score" in sql[0] and "ml.score_history" in sql[1]
    assert engine.connection.calls[0][1]["is_hot"] is True
    await repo.mark_stale(COMPANY)
    assert "SET stale = true" in engine.connection.calls[-1][0]
    await repo.close()
    assert engine.disposed


async def test_ml_repository_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "company_id": COMPANY,
        "score": 7,
        "stale": False,
        "score_version": "r",
        "source_version": "s",
    }
    repo, _engine = _ml_repo(monkeypatch, [FakeResult([row]), FakeResult()])
    assert await repo.latest_score(COMPANY) == row
    assert await repo.latest_score(COMPANY) is None
    assert await repo.load_weights(TENANT) is None


async def test_ml_load_weights_resolves_rows_from_core(monkeypatch: pytest.MonkeyPatch) -> None:
    class Rules:
        async def get_rules(self, _tenant_id: UUID) -> Any:
            return [("icp_fit", 0.5, True), ("territory", 0.9, False)]

        async def close(self) -> None:
            return None

    repo, _engine = _ml_repo(monkeypatch)
    repo._core_rules = Rules()
    weights = await repo.load_weights(TENANT)
    assert weights.icp_fit == 0.5
    assert weights.territory != 0.9
    await repo.close()


async def test_ml_repository_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _engine = _ml_repo(monkeypatch, engine=False)
    assert await repo.latest_score(COMPANY) is None
    assert await repo.load_weights(TENANT) is None
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await repo.mark_stale(COMPANY)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await repo.upsert_score(
            company_id=COMPANY, score=1, is_hot=False, source_version="a", score_version="b"
        )
    await repo.close()


# --------------------------------------------------------------------------- ml main


class _MlRepoStub:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.error: Exception | None = None
        self.score: dict[str, object] | None = None
        self.weights_for: list[UUID] = []
        self.upserts: list[dict[str, Any]] = []
        self.closed = False

    async def load_weights(self, tenant_id: UUID) -> Any:
        self.weights_for.append(tenant_id)
        return None

    async def upsert_score(self, **kwargs: Any) -> None:
        if self.error:
            raise self.error
        self.upserts.append(kwargs)

    async def latest_score(self, company_id: UUID) -> dict[str, object] | None:
        if self.error:
            raise self.error
        return self.score

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def ml_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(ml_main, "MlRepository", _MlRepoStub)
    monkeypatch.setattr(ml_main, "create_redis", lambda _url: FakeRedisWrapper(redis))
    return ml_main.app


FEATURES = {
    "company_id": str(COMPANY),
    "icp_fit": 0.9,
    "signal_strength": 0.8,
    "territory_opportunity": 0.7,
}


async def test_ml_ready_and_health(
    ml_app: Any, asgi_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def up(_url: Any) -> bool:
        return True

    async def down(_url: Any) -> bool:
        return False

    monkeypatch.setattr(ml_main, "check_database", up)
    monkeypatch.setattr(ml_main, "check_redis", up)
    async with asgi_client(ml_app) as client:
        assert (await client.get("/health")).json()["service"] == "ml"
        assert (await client.get("/ready")).json()["status"] == "ready"
        monkeypatch.setattr(ml_main, "check_database", down)
        degraded = await client.get("/ready")
        assert degraded.status_code == 503 and degraded.json()["status"] == "degraded"
    assert ml_app.state.repository.closed


async def test_ml_regenerate_score_with_and_without_tenant(ml_app: Any, asgi_client: Any) -> None:
    async with asgi_client(ml_app) as client:
        repo = ml_app.state.repository
        plain = await client.post("/internal/ml/scores", json=FEATURES, headers=_headers())
        assert plain.status_code == 200 and plain.json()["status"] == "computed"
        assert repo.weights_for == []
        tenant = {**_headers(), "X-Tenant-Id": str(TENANT)}
        scoped = await client.post("/internal/ml/scores", json=FEATURES, headers=tenant)
        assert scoped.status_code == 200
        assert repo.weights_for == [TENANT]
        assert len(repo.upserts) == 2
        repo.error = RuntimeError("DATABASE_URL is required for ml writes")
        failed = await client.post("/internal/ml/scores", json=FEATURES, headers=_headers())
        assert failed.status_code == 503


async def test_ml_get_score_paths(ml_app: Any, asgi_client: Any) -> None:
    url = f"/internal/ml/scores/{COMPANY}"
    async with asgi_client(ml_app) as client:
        repo = ml_app.state.repository
        assert (await client.get(url, headers=_headers())).status_code == 404
        repo.score = {"company_id": str(COMPANY), "score": 9}
        found = await client.get(url, headers=_headers())
        assert found.json()["score"] == 9
        repo.error = RuntimeError("no db")
        assert (await client.get(url, headers=_headers())).status_code == 503


# --------------------------------------------------------------------------- segmentation


def _small_dataset() -> dict[str, list[dict[str, Any]]]:
    return build_dataset(city="all", orgs=30, days=30, seed=3)


def test_segmentation_edge_cases() -> None:
    assert seg.k_prototypes([], 3) == []
    assert seg._default_k(1) == 1
    assert seg.adjusted_rand_index([0], [0]) == 1.0
    assert seg.adjusted_rand_index([0, 0, 1, 1], [0, 0, 1, 1]) == 1.0
    assert seg.segment_name(-1) == "noise" and seg.segment_name(3) == "segment-03"
    empty = seg.run_segmentation({"companies": [], "signals": []}, as_of=date(2026, 1, 1))
    assert empty.metrics["mean_gower_to_global"] == 0.0
    assert empty.segments == {}


def test_k_prototypes_skips_empty_cluster() -> None:
    row = {
        "employee_count": 1.0,
        "signal_count": 1.0,
        "mean_strength": 1.0,
        "signal_types": 1.0,
        "has_website": 1.0,
        "city": "A",
        "sector_code": "x",
    }
    # Identical rows force every point into cluster 0, leaving cluster 1 empty.
    labels = seg.k_prototypes([dict(row) for _ in range(4)], 2, seed=1)
    assert set(labels) == {0}


def _install_fake_ml_extra(monkeypatch: pytest.MonkeyPatch, labels: list[int] | None) -> None:
    hdbscan = types.ModuleType("hdbscan")
    umap = types.ModuleType("umap")
    numpy = types.ModuleType("numpy")
    sklearn = types.ModuleType("sklearn")
    preprocessing = types.ModuleType("sklearn.preprocessing")

    class HDBSCAN:
        def __init__(self, min_cluster_size: int) -> None:
            self.size = min_cluster_size

        def fit_predict(self, embedding: Any) -> list[int]:
            return labels if labels is not None else [i % 2 - 1 for i in range(len(embedding))]

    class UMAP:
        def __init__(self, **_k: Any) -> None:
            pass

        def fit_transform(self, data: Any) -> Any:
            return data

    class StandardScaler:
        def fit_transform(self, data: Any) -> Any:
            return data

    hdbscan.HDBSCAN = HDBSCAN  # type: ignore[attr-defined]
    umap.UMAP = UMAP  # type: ignore[attr-defined]
    numpy.array = lambda m: m  # type: ignore[attr-defined]
    preprocessing.StandardScaler = StandardScaler  # type: ignore[attr-defined]
    for name, module in {
        "hdbscan": hdbscan,
        "umap": umap,
        "numpy": numpy,
        "sklearn": sklearn,
        "sklearn.preprocessing": preprocessing,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def test_umap_hdbscan_path_uses_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_ml_extra(monkeypatch, None)
    result = seg.run_segmentation(_small_dataset(), as_of=date(2026, 1, 1))
    assert result.algorithm == seg.ALGORITHM
    assert result.metrics["noise"] > 0
    assert "umap-hdbscan" in result.model_version


def test_default_clusterer_falls_back_without_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("hdbscan", "umap"):
        monkeypatch.setitem(sys.modules, name, None)
    rows = list(seg.build_features(_small_dataset()).values())
    algorithm, labels = seg.default_clusterer(rows, seg.SEED)
    assert algorithm == seg.FALLBACK_ALGORITHM
    assert len(labels) == len(rows)


async def test_sql_segment_store_saves_all_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine()
    import libs.common.db as db

    monkeypatch.setattr(db, "create_engine", lambda *_a, **_k: engine)
    store = seg.SqlSegmentStore("postgresql://x/y")
    result = await seg.run_batch(store, _small_dataset(), seed=5)
    calls = engine.connection.calls
    assert "UPDATE ml.model_version SET active = false" in calls[0][0]
    assert calls[1][1]["v"] == result.model_version
    segments = [c for c in calls if "INSERT INTO ml.company_segment" in c[0]]
    assert len(segments) == len(result.segments) == 30
    assert len([c for c in calls if "INSERT INTO ml.feature_store" in c[0]]) == 30
    await store.close()
    assert engine.disposed


async def test_sql_segment_store_requires_database(monkeypatch: pytest.MonkeyPatch) -> None:
    import libs.common.db as db

    monkeypatch.setattr(db, "create_engine", lambda *_a, **_k: None)
    store = seg.SqlSegmentStore(None)
    result = seg.run_segmentation(_small_dataset(), as_of=date(2026, 1, 1))
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await store.save(result)
    await store.close()


def test_segmentation_main_prints_metrics(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    saved: list[Any] = []

    class Store:
        def __init__(self, _url: str | None) -> None:
            pass

        async def save(self, result: Any) -> None:
            saved.append(result)

    monkeypatch.setattr(seg, "SqlSegmentStore", Store)
    monkeypatch.setattr(sys, "argv", ["segmentation", "--orgs", "20", "--seed", "4"])
    assert seg.main() == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["seed"] == 4 and printed["companies"] == 20
    assert saved and printed["model_version"] == saved[0].model_version


# --------------------------------------------------------------------------- pulse rollup close


def test_default_close_day_is_previous_local_day() -> None:
    assert rollup_close.default_close_day(datetime(2026, 3, 10, 18, 0, tzinfo=UTC)) == date(
        2026, 3, 9
    )
    assert isinstance(rollup_close.default_close_day(), date)


async def test_close_day_sets_tenant_then_updates() -> None:
    connection = FakeConnection()
    await rollup_close.close_day(connection, TENANT, date(2026, 3, 9))  # type: ignore[arg-type]
    assert "set_config" in connection.calls[0][0]
    assert connection.calls[0][1] == {"tenant_id": str(TENANT)}
    assert "UPDATE pulse.daily_rollup" in connection.calls[1][0]
    assert connection.calls[1][1]["threshold"] == rollup_close.LOW_SAMPLE_THRESHOLD


def test_rollup_close_parse_args() -> None:
    args = rollup_close.parse_args(["--tenant", str(TENANT), "--day", "2026-03-09"])
    assert args.tenant == [TENANT] and args.day == date(2026, 3, 9)
    with pytest.raises(SystemExit):
        rollup_close.parse_args([])


async def test_rollup_close_main_closes_each_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(rollup_close, "create_engine", lambda *_a, **_k: engine)
    other = uuid4()
    await rollup_close.main(
        ["--tenant", str(TENANT), "--tenant", str(other), "--day", "2026-03-09"]
    )
    updates = [c for c in engine.connection.calls if "UPDATE pulse.daily_rollup" in c[0]]
    assert [u[1]["tenant_id"] for u in updates] == [TENANT, other]
    assert engine.disposed
    await rollup_close.main(["--tenant", str(TENANT)])
    assert engine.connection.calls[-1][1]["rollup_date"] == rollup_close.default_close_day()


async def test_rollup_close_main_requires_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rollup_close, "create_engine", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await rollup_close.main(["--tenant", str(TENANT)])


# --------------------------------------------------------------------------- core dead letter


class _Tx:
    def __init__(self, rows: list[dict[str, Any]], *, fail_publish_of: set[int]) -> None:
        self.rows = rows
        self.published: list[int] = []
        self.dead: list[dict[str, Any]] = []
        self.failures: list[int] = []
        self.fail_publish_of = fail_publish_of

    async def claim_due(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        return self.rows

    async def mark_published(self, row_id: int, now: datetime) -> None:
        self.published.append(row_id)

    async def record_failure(self, row_id: int, **_k: Any) -> None:
        self.failures.append(row_id)

    async def dead_letter(self, row: dict[str, Any], **_k: Any) -> None:
        self.dead.append(row)


def _outbox_row(row_id: int) -> dict[str, Any]:
    return {
        "id": row_id,
        "event_id": uuid4(),
        "tenant_id": TENANT,
        "event_type": "signal.created",
        "aggregate_type": "company",
        "aggregate_id": COMPANY,
        "version": 1,
        "payload": {"k": "v"},
        "attempts": 0,
        "replay": row_id == 2,
    }


class _CoreRepoStub:
    instances: list["_CoreRepoStub"] = []

    def __init__(self, _url: str | None) -> None:
        self.passes = 0
        self.closed = False
        _CoreRepoStub.instances.append(self)

    def outbox_unit_of_work(self) -> AsyncExit:
        self.passes += 1
        if self.passes == 1:
            raise RuntimeError("database down")
        if self.passes == 2:
            return AsyncExit(_Tx([_outbox_row(1), _outbox_row(2)], fail_publish_of=set()))
        if self.passes == 3:
            raise Stop()
        return AsyncExit(_Tx([], fail_publish_of=set()))

    async def list_outbox_events_since(self, *_a: Any) -> list[dict[str, Any]]:
        return []

    async def close(self) -> None:
        self.closed = True


def _patch_core_io(monkeypatch: pytest.MonkeyPatch, redis: Any) -> None:
    import libs.common.redis_client as redis_client
    import services.core.app.repository as core_repository

    _CoreRepoStub.instances = []
    monkeypatch.setattr(redis_client, "create_redis", lambda _url: redis)
    monkeypatch.setattr(core_repository, "CoreRepository", _CoreRepoStub)
    monkeypatch.setattr(
        "libs.common.settings.get_settings", lambda: SimpleNamespace(**vars(make_settings()))
    )


async def test_dead_letter_main_survives_a_failed_pass_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = fakeredis.FakeAsyncRedis(decode_responses=True)
    redis = FakeRedisWrapper(inner)
    _patch_core_io(monkeypatch, redis)
    with pytest.raises(Stop):
        await dead_letter.main()
    # pass 1 fails, pass 2 publishes, pass 3 stops the loop, pass 4 is the shutdown flush
    assert _CoreRepoStub.instances[0].passes == 4
    assert redis.closed and _CoreRepoStub.instances[0].closed
    entries = await inner.xrange("invent.events")
    assert len(entries) == 2
    assert [e[1].get("replay") for e in entries] == [None, "true"]
    assert entries[0][1]["tenant_id"] == str(TENANT)


def test_dead_letter_retry_delay_and_fields() -> None:
    assert dead_letter.retry_delay(0) is None
    assert dead_letter.retry_delay(5) is not None
    assert dead_letter.retry_delay(6) is None
    row = _outbox_row(1)
    assert dead_letter.stream_fields(row, target="ml")["target"] == "ml"
    row["tenant_id"] = None
    with pytest.raises(dead_letter.MissingTenantError):
        dead_letter.stream_fields(row)


# --------------------------------------------------------------------------- core republish


async def test_redis_locker_roundtrip() -> None:
    locker = republish.RedisLocker(fakeredis.FakeAsyncRedis(decode_responses=True))
    key = republish.lock_key("ml")
    assert await locker.acquire(key, 60)
    assert not await locker.acquire(key, 60)
    assert await republish.is_target_locked(locker, "ml")
    await locker.release(key)
    assert not await republish.is_target_locked(locker, "ml")


async def test_open_io_yields_pager_publisher_and_locker(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = fakeredis.FakeAsyncRedis(decode_responses=True)
    redis = FakeRedisWrapper(inner)
    _patch_core_io(monkeypatch, redis)
    async with republish.open_io() as (fetch_page, publish, locker):
        assert await fetch_page(NOW, None, 0, 10) == []
        await publish({"event_id": "e1"})
        assert await locker.acquire("k", 10)
    assert redis.closed and _CoreRepoStub.instances[0].closed
    assert len(await inner.xrange("invent.events")) == 1


async def test_republish_run_reports_count_and_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    published: list[dict[str, str]] = []
    pages = [[_outbox_row(1), {**_outbox_row(2), "tenant_id": None}], []]

    async def fetch_page(*_a: Any) -> list[dict[str, Any]]:
        return pages.pop(0)

    async def publish(fields: dict[str, str]) -> None:
        published.append(fields)

    locks: set[str] = set()

    class Locker:
        async def acquire(self, key: str, ttl_seconds: int) -> bool:
            locks.add(key)
            return True

        async def release(self, key: str) -> None:
            locks.discard(key)

        async def exists(self, key: str) -> bool:
            return key in locks

    monkeypatch.setattr(republish, "open_io", lambda: AsyncExit((fetch_page, publish, Locker())))
    code = await republish.run(["--target", "pulse", "--since", "2026-01-01", "--confirm"])
    assert code == 0
    assert "replayed 1 events to pulse" in capsys.readouterr().out
    assert published[0]["target"] == "pulse" and published[0]["replay"] == "true"
    assert not locks

    assert await republish.run(["--target", "pulse", "--since", "2026-01-01"]) == 2
    assert "--confirm" in capsys.readouterr().err
    assert await republish.run(["--target", "ml", "--since", "not-a-date", "--confirm"]) == 2
    assert "invalid --since" in capsys.readouterr().err


async def test_relay_due_events_retries_dead_letters_and_keeps_order() -> None:
    no_tenant = {**_outbox_row(1), "tenant_id": None}
    first = _outbox_row(2)
    first["aggregate_id"] = uuid4()
    later = {**_outbox_row(3), "aggregate_id": first["aggregate_id"]}
    exhausted = {**_outbox_row(4), "aggregate_id": uuid4(), "attempts": 5}
    fine = {**_outbox_row(5), "aggregate_id": uuid4()}
    tx = _Tx([no_tenant, first, later, exhausted, fine], fail_publish_of={2, 4})
    calls: list[int] = []

    async def publish(row: dict[str, Any]) -> None:
        calls.append(row["id"])
        if row["id"] in tx.fail_publish_of:
            raise ConnectionError("redis down")

    counts = await dead_letter.relay_due_events(AsyncExit(tx), publish, now=NOW)
    assert counts == {"published": 1, "retried": 1, "dead_lettered": 2}
    assert calls == [2, 4, 5]
    assert tx.failures == [2]
    assert [r["id"] for r in tx.dead] == [1, 4]
    assert tx.published == [5]
