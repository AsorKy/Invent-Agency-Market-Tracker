"""Prove signal.scored leaves ml through its outbox and the shared relay (T-176, D-21).

In-memory fakes stand in for the database and Redis. They show: the score and its outbox rows are
handed to the store together (one transaction in ``MlRepository``), flag off changes nothing,
the ml relay instance publishes the row with its tenant through the shared relay, and every SQL
statement of the ml relay touches only ``ml.*`` (INV-01).
"""

import asyncio
import json
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

from libs.common.settings import get_settings
from libs.contracts.api import ScoreFeatures
from libs.messaging.relay import BACKOFF_SECONDS, relay_due_events, stream_fields
from services.ml.app import outbox_relay as ml_outbox_relay
from services.ml.app import repository as repository_module
from services.ml.app import scoring_service as scoring_module
from services.ml.app.repository import MlOutboxUnitOfWork, MlRepository, insert_outbox_events
from services.ml.app.scoring_service import ScoringService, chain_outbox_events

TENANT = uuid4()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeMlOutbox:
    """In-memory ``ml.outbox_event`` and ``ml.outbox_dead_letter`` with the SQL bookkeeping."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.dead: list[dict[str, Any]] = []

    def add(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            self.rows.append({"id": len(self.rows) + 1, "attempts": 0, **event})

    def unit_of_work(self) -> "FakeUow":
        return FakeUow(self)


class FakeUow:
    def __init__(self, outbox: FakeMlOutbox) -> None:
        self.o = outbox

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def claim_due(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.o.rows
            if not r.get("published_at")
            and not r.get("dead_lettered_at")
            and not (r.get("next_attempt_at") and r["next_attempt_at"] > now)
        ][:limit]

    def _row(self, row_id: int) -> dict[str, Any]:
        return next(r for r in self.o.rows if r["id"] == row_id)

    async def mark_published(self, row_id: int, now: datetime) -> None:
        self._row(row_id)["published_at"] = now

    async def record_failure(
        self, row_id: int, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None:
        self._row(row_id).update(attempts=attempts, next_attempt_at=next_attempt_at)

    async def dead_letter(
        self, row: dict[str, Any], *, attempts: int, error: str, now: datetime
    ) -> None:
        self.o.dead.append({"event_id": row["event_id"], "tenant_id": row["tenant_id"]})
        self._row(row["id"]).update(attempts=attempts, dead_lettered_at=now)


class OutboxStore:
    """Score store that keeps the outbox rows it was handed with the score."""

    def __init__(self, outbox: FakeMlOutbox) -> None:
        self.outbox = outbox
        self.calls: list[dict[str, Any]] = []

    async def latest_score(self, _company_id: UUID) -> None:
        return None

    async def upsert_score(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        self.outbox.add(kwargs.get("outbox_events") or [])


class FakeRedis:
    def __init__(self) -> None:
        self.stream: list[dict[str, str]] = []

    async def set(self, *_a: Any, **_k: Any) -> bool:
        return True

    async def eval(self, *_a: Any, **_k: Any) -> int:
        return 1

    async def xadd(self, _name: str, fields: dict[str, str]) -> str:
        self.stream.append(fields)
        return "1-0"


def _flag(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    settings = get_settings().model_copy(update={"event_chain_emit": enabled})
    monkeypatch.setattr(scoring_module, "get_settings", lambda: settings)


def _features(company: UUID, parent: UUID | None = None) -> ScoreFeatures:
    return ScoreFeatures(
        company_id=company,
        icp_fit=0.9,
        signal_strength=0.8,
        territory_opportunity=0.7,
        parent_event_id=parent,
    )


async def test_flag_off_writes_no_outbox_row_and_passes_no_outbox_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, False)
    outbox, redis = FakeMlOutbox(), FakeRedis()
    store = OutboxStore(outbox)
    await ScoringService(store, redis, lock_ttl_seconds=5).regenerate(  # type: ignore[arg-type]
        _features(uuid4()), None, TENANT
    )
    assert "outbox_events" not in store.calls[0]
    assert outbox.rows == [] and outbox.dead == []
    assert [e["event_type"] for e in redis.stream] == ["lead.score_updated"]


async def test_flag_on_hands_signal_scored_to_the_store_and_not_to_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, True)
    outbox, redis = FakeMlOutbox(), FakeRedis()
    store = OutboxStore(outbox)
    company, parent = uuid4(), uuid4()
    await ScoringService(store, redis, lock_ttl_seconds=5).regenerate(  # type: ignore[arg-type]
        _features(company, parent), None, TENANT
    )
    assert [e["event_type"] for e in redis.stream] == ["lead.score_updated"]
    assert len(outbox.rows) == 1
    row = outbox.rows[0]
    assert row["event_type"] == "signal.scored" and row["tenant_id"] == TENANT
    assert row["aggregate_id"] == company and row["payload"]["parent_event_id"] == str(parent)


async def test_ml_relay_publishes_the_row_with_its_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, True)
    outbox, redis = FakeMlOutbox(), FakeRedis()
    company = uuid4()
    await ScoringService(OutboxStore(outbox), redis, lock_ttl_seconds=5).regenerate(  # type: ignore[arg-type]
        _features(company), None, TENANT
    )
    published = FakeRedis()

    async def publish(row: dict[str, Any]) -> None:
        await published.xadd("invent.events", stream_fields(row))

    counts = await relay_due_events(outbox.unit_of_work(), publish, now=T0)
    assert counts == {"published": 1, "retried": 0, "dead_lettered": 0}
    entry = published.stream[0]
    assert entry["tenant_id"] == str(TENANT) and entry["event_type"] == "signal.scored"
    assert entry["aggregate_id"] == str(company)
    assert json.loads(entry["payload"])["company_id"] == str(company)
    assert await relay_due_events(outbox.unit_of_work(), publish, now=T0) == {
        "published": 0,
        "retried": 0,
        "dead_lettered": 0,
    }


async def test_ml_relay_retries_then_dead_letters_in_the_ml_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, True)
    outbox = FakeMlOutbox()
    await ScoringService(OutboxStore(outbox), FakeRedis(), lock_ttl_seconds=5).regenerate(  # type: ignore[arg-type]
        _features(uuid4()), None, TENANT
    )

    async def broken(_row: dict[str, Any]) -> None:
        raise ConnectionError("redis down")

    clock = T0
    for attempt in range(len(BACKOFF_SECONDS)):
        counts = await relay_due_events(outbox.unit_of_work(), broken, now=clock)
        assert counts["retried"] == 1, attempt
        clock = outbox.rows[0]["next_attempt_at"]
    counts = await relay_due_events(outbox.unit_of_work(), broken, now=clock)
    assert counts["dead_lettered"] == 1 and len(outbox.dead) == 1


def test_chain_outbox_events_without_tenant_writes_nothing() -> None:
    assert (
        chain_outbox_events(company_id=uuid4(), tenant_id=None, score=5, source_version="1") == []
    )


class Recorder:
    """Fake async engine that records statements and whether the transaction committed."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.outcome = "open"
        self.fail_on = fail_on

    def begin(self) -> "Recorder":
        return self

    async def __aenter__(self) -> "Recorder":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.outcome = "rollback" if exc_type else "commit"

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("outbox insert failed")
        self.statements.append((sql, params or {}))
        return None


async def test_score_and_outbox_rows_share_one_transaction() -> None:
    recorder = Recorder()
    repository = MlRepository(None)
    repository.engine = recorder  # type: ignore[assignment]
    events = chain_outbox_events(company_id=uuid4(), tenant_id=TENANT, score=70, source_version="2")
    await repository.upsert_score(
        company_id=uuid4(),
        score=70,
        is_hot=False,
        source_version="2",
        score_version="v1",
        outbox_events=events,
    )
    tables = [s.split("INTO")[1].split()[0] for s, _ in recorder.statements]
    assert tables == ["ml.lead_score", "ml.score_history", "ml.outbox_event"]
    assert recorder.outcome == "commit"
    assert recorder.statements[2][1]["tenant_id"] == TENANT


async def test_failed_outbox_insert_rolls_the_score_back() -> None:
    recorder = Recorder(fail_on="ml.outbox_event")
    repository = MlRepository(None)
    repository.engine = recorder  # type: ignore[assignment]
    events = chain_outbox_events(company_id=uuid4(), tenant_id=TENANT, score=70, source_version="2")
    with pytest.raises(RuntimeError):
        await repository.upsert_score(
            company_id=uuid4(),
            score=70,
            is_hot=False,
            source_version="2",
            score_version="v1",
            outbox_events=events,
        )
    assert recorder.outcome == "rollback"


async def test_upsert_without_events_writes_no_outbox_row() -> None:
    recorder = Recorder()
    repository = MlRepository(None)
    repository.engine = recorder  # type: ignore[assignment]
    await repository.upsert_score(
        company_id=uuid4(), score=1, is_hot=False, source_version="1", score_version="v1"
    )
    assert all("outbox" not in sql for sql, _ in recorder.statements)


async def test_every_relay_statement_touches_only_the_ml_schema() -> None:
    recorder = Recorder()
    transaction = repository_module.MlOutboxTransaction(recorder)  # type: ignore[arg-type]

    class Rows:
        def mappings(self) -> "Rows":
            return self

        def all(self) -> list[Any]:
            return []

    async def execute(statement: Any, params: Any = None) -> Rows:
        recorder.statements.append((str(statement), params or {}))
        return Rows()

    recorder.execute = execute  # type: ignore[method-assign]
    row = {"id": 1, "event_id": uuid4(), "tenant_id": TENANT, "event_type": "x", "payload": {}}
    await transaction.claim_due(T0, 10)
    await transaction.mark_published(1, T0)
    await transaction.record_failure(1, attempts=1, next_attempt_at=T0, error="e")
    await transaction.dead_letter(row, attempts=6, error="e", now=T0)
    await insert_outbox_events(
        recorder,  # type: ignore[arg-type]
        chain_outbox_events(company_id=uuid4(), tenant_id=TENANT, score=1, source_version="1"),
        T0,
    )
    assert len(recorder.statements) == 6
    for sql, _ in recorder.statements:
        assert "ml." in sql
        for other in ("core.", "pulse.", "ingest.", "brief.", "identity."):
            assert other not in sql


async def test_unit_of_work_commits_and_rolls_back_with_the_pass() -> None:
    recorder = Recorder()
    unit = MlOutboxUnitOfWork(recorder)  # type: ignore[arg-type]
    async with unit:
        pass
    assert recorder.outcome == "commit"
    with pytest.raises(ValueError):
        async with MlOutboxUnitOfWork(recorder):  # type: ignore[arg-type]
            raise ValueError("boom")
    assert recorder.outcome == "rollback"


def test_ml_relay_main_runs_the_shared_loop_with_the_ml_unit_of_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import libs.messaging.relay as shared

    seen: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(shared, "run_relay_loop", fake_loop)
    asyncio.run(ml_outbox_relay.main())
    assert seen["stream_name"] == "invent.events" and seen["service_name"] == "ml-relay"
    assert isinstance(seen["open_unit_of_work"].__self__, MlRepository)
