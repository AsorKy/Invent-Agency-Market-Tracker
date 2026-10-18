"""Unit tests for score single-flight and publication (REQ-ML-001).

Uses in-memory fakes for the score store and Redis; no live services.
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

from libs.contracts.api import ScoreFeatures
from services.ml.app.scoring_service import ScoringService


class FakeRedis:
    def __init__(self) -> None:
        self.locks: dict[str, str] = {}
        self.events: list[dict[str, str]] = []

    async def set(self, name: str, value: str, *, nx: bool, px: int) -> bool:
        if nx and name in self.locks:
            return False
        self.locks[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        key, owner = keys_and_args
        if self.locks.get(key) == owner:
            del self.locks[key]
            return 1
        return 0

    async def xadd(self, name: str, fields: dict[str, str]) -> str:
        self.events.append(fields)
        return "1-0"


class FakeStore:
    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.rows: dict[UUID, dict[str, Any]] = {}
        self.upserts = 0
        self.gate = gate
        self.fail = False

    async def latest_score(self, company_id: UUID) -> dict[str, Any] | None:
        return self.rows.get(company_id)

    async def upsert_score(
        self,
        *,
        company_id: UUID,
        score: int,
        is_hot: bool,
        source_version: str,
        score_version: str,
    ) -> None:
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("db down")
        self.upserts += 1
        self.rows[company_id] = {
            "score": score,
            "stale": False,
            "score_version": score_version,
            "source_version": source_version,
        }

    async def mark_stale(self, company_id: UUID) -> None:
        self.rows[company_id]["stale"] = True


def _features(company_id: UUID, version: str = "1") -> ScoreFeatures:
    return ScoreFeatures(
        company_id=company_id,
        icp_fit=1,
        signal_strength=1,
        territory_opportunity=1,
        source_version=version,
    )


def test_compute_publishes_and_releases_lock() -> None:
    redis, store, company = FakeRedis(), FakeStore(), uuid4()
    service = ScoringService(store, redis, lock_ttl_seconds=30)
    result = asyncio.run(service.regenerate(_features(company)))
    assert result.status == "computed"
    assert result.score == 100
    assert not result.stale
    assert redis.locks == {}
    assert [e["event_type"] for e in redis.events] == ["lead.score_updated"]
    assert redis.events[0]["aggregate_id"] == str(company)


def test_concurrent_call_gets_previous_stale_score() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        redis, store, company = FakeRedis(), FakeStore(gate), uuid4()
        store.rows[company] = {"score": 40, "score_version": "rule-f1:0", "source_version": "0"}
        service = ScoringService(store, redis, lock_ttl_seconds=30)
        first = asyncio.create_task(service.regenerate(_features(company)))
        await asyncio.sleep(0)
        second = await service.regenerate(_features(company))
        assert second.status == "in_progress"
        assert second.stale
        assert second.score == 40
        assert second.score_version == "rule-f1:0"
        gate.set()
        assert (await first).status == "computed"
        assert store.upserts == 1
        assert len(redis.events) == 1

    asyncio.run(scenario())


def test_locked_without_previous_score_is_pending() -> None:
    redis, store, company = FakeRedis(), FakeStore(), uuid4()
    redis.locks[f"lock:score:{company}"] = "other"
    service = ScoringService(store, redis, lock_ttl_seconds=30)
    result = asyncio.run(service.regenerate(_features(company)))
    assert (result.score, result.score_version, result.stale) == (0, "pending", True)
    assert redis.locks[f"lock:score:{company}"] == "other"


def test_lock_released_on_failure_without_event() -> None:
    redis, store, company = FakeRedis(), FakeStore(), uuid4()
    store.fail = True
    service = ScoringService(store, redis, lock_ttl_seconds=30)
    try:
        asyncio.run(service.regenerate(_features(company)))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
    assert redis.locks == {}
    assert redis.events == []


def test_source_version_change_marks_stale() -> None:
    async def scenario() -> None:
        redis, store, company = FakeRedis(), FakeStore(), uuid4()
        service = ScoringService(store, redis, lock_ttl_seconds=30)
        assert not await service.refresh_staleness(company, "1")
        await service.regenerate(_features(company, "1"))
        assert not await service.refresh_staleness(company, "1")
        assert not store.rows[company]["stale"]
        assert await service.refresh_staleness(company, "2")
        assert store.rows[company]["stale"]

    asyncio.run(scenario())
