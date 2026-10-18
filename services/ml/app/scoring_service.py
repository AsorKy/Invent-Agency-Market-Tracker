"""Regenerate lead scores under a Redis single-flight lock.

``ScoringService`` takes the lock ``lock:score:<company_id>``, computes the
pure rule from ``libs/domain/scoring.py``, stores the score through the
repository, and publishes ``lead.score_updated`` to the event stream. A caller
that loses the lock receives the previous score marked stale. It relates to
``services/ml/app/main.py`` and ``repository.py`` and keeps a burst of signals
from triggering concurrent regenerations of one score (REQ-ML-001).
"""

from typing import Any, Protocol
from uuid import UUID, uuid4

from libs.contracts.api import ScoreFeatures, ScoreResponse
from libs.contracts.events import EventType
from libs.domain.scoring import RULE_VERSION, ScoreWeights, calculate_rule_score, is_stale

STREAM_NAME = "invent.events"
RELEASE_LOCK_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


class ScoreStore(Protocol):
    async def latest_score(self, company_id: UUID) -> dict[str, Any] | None: ...

    async def upsert_score(
        self,
        *,
        company_id: UUID,
        score: int,
        is_hot: bool,
        source_version: str,
        score_version: str,
    ) -> None: ...

    async def mark_stale(self, company_id: UUID) -> None: ...


class ScoreRedis(Protocol):
    async def set(self, name: str, value: str, *, nx: bool, px: int) -> Any: ...

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any: ...

    async def xadd(self, name: str, fields: dict[str, str]) -> Any: ...


class ScoringService:
    def __init__(self, store: ScoreStore, redis: ScoreRedis, *, lock_ttl_seconds: int) -> None:
        self._store = store
        self._redis = redis
        self._lock_ttl_ms = lock_ttl_seconds * 1000

    async def regenerate(
        self, features: ScoreFeatures, weights: ScoreWeights | None = None
    ) -> ScoreResponse:
        """Compute and persist the score, or return the stale previous one."""
        lock_key = f"lock:score:{features.company_id}"
        owner_id = str(uuid4())
        acquired = await self._redis.set(lock_key, owner_id, nx=True, px=self._lock_ttl_ms)
        if not acquired:
            previous = await self._store.latest_score(features.company_id)
            return ScoreResponse(
                company_id=features.company_id,
                score=int(previous["score"]) if previous else 0,
                status="in_progress",
                stale=True,
                score_version=str(previous["score_version"]) if previous else "pending",
            )
        try:
            breakdown = calculate_rule_score(
                icp_fit=features.icp_fit,
                signal_strength=features.signal_strength,
                territory_opportunity=features.territory_opportunity,
                weights=weights,
            )
            score_version = f"{RULE_VERSION}:{features.source_version}"
            await self._store.upsert_score(
                company_id=features.company_id,
                score=breakdown.score,
                is_hot=breakdown.is_hot,
                source_version=features.source_version,
                score_version=score_version,
            )
            await self._redis.xadd(
                STREAM_NAME,
                {
                    "event_id": str(uuid4()),
                    "event_type": EventType.LEAD_SCORE_UPDATED.value,
                    "aggregate_type": "company",
                    "aggregate_id": str(features.company_id),
                    "score": str(breakdown.score),
                    "is_hot": str(breakdown.is_hot).lower(),
                    "source_version": features.source_version,
                },
            )
            return ScoreResponse(
                company_id=features.company_id,
                score=breakdown.score,
                status="computed",
                score_version=score_version,
            )
        finally:
            await self._redis.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, owner_id)

    async def refresh_staleness(self, company_id: UUID, current_source_version: str) -> bool:
        """Mark the stored score stale when its source version changed."""
        previous = await self._store.latest_score(company_id)
        if previous is None:
            return False
        if is_stale(str(previous.get("source_version")), current_source_version):
            await self._store.mark_stale(company_id)
            return True
        return False
