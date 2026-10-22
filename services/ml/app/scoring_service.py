"""Regenerate lead scores under a Redis single-flight lock.

``ScoringService`` takes the lock ``lock:score:<company_id>``, computes the
pure rule from ``libs/domain/scoring.py``, stores the score through the
repository, and publishes ``lead.score_updated`` to the event stream. A caller
that loses the lock receives the previous score marked stale. It relates to
``services/ml/app/main.py`` and ``repository.py`` and keeps a burst of signals
from triggering concurrent regenerations of one score (REQ-ML-001).
"""

import json
import logging
from typing import Any, Protocol
from uuid import UUID, uuid4

from libs.contracts.api import ScoreFeatures, ScoreResponse
from libs.contracts.events import EventType
from libs.domain.scoring import (
    RulesSource,
    ScoreWeights,
    calculate_rule_score,
    is_stale,
    rule_version,
    rules_source_of,
)

STREAM_NAME = "invent.events"
EVENT_VERSION = 1
_log = logging.getLogger(__name__)
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
        features: tuple[float, float, float] | None = None,
        weights: tuple[float, float, float] | None = None,
    ) -> None: ...

    async def mark_stale(self, company_id: UUID) -> None: ...


class ScoreRedis(Protocol):
    async def set(self, name: str, value: str, *, nx: bool, px: int) -> Any: ...

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any: ...

    async def xadd(self, name: str, fields: dict[str, str]) -> Any: ...


def score_event_fields(
    *, company_id: UUID, tenant_id: UUID | None, score: int, is_hot: bool, source_version: str
) -> dict[str, str]:
    """Build the stream entry of ``lead.score_updated`` in the core relay envelope.

    Same fields as ``services/core/app/dead_letter.stream_fields`` (``tenant_id``, ``payload``,
    ``version`` and the aggregate keys). ``score``, ``is_hot`` and ``source_version`` stay as
    top-level fields because the core qualification worker reads them there. Without a tenant
    the ``tenant_id`` field is omitted and consumers skip the entry.
    """
    fields = {
        "event_id": str(uuid4()),
        "event_type": EventType.LEAD_SCORE_UPDATED.value,
        "aggregate_type": "company",
        "aggregate_id": str(company_id),
        "version": str(EVENT_VERSION),
        "payload": json.dumps(
            {
                "company_id": str(company_id),
                "score": score,
                "is_hot": is_hot,
                "source_version": source_version,
            }
        ),
        "score": str(score),
        "is_hot": str(is_hot).lower(),
        "source_version": source_version,
    }
    if tenant_id is not None:
        fields["tenant_id"] = str(tenant_id)
    return fields


class ScoringService:
    def __init__(self, store: ScoreStore, redis: ScoreRedis, *, lock_ttl_seconds: int) -> None:
        self._store = store
        self._redis = redis
        self._lock_ttl_ms = lock_ttl_seconds * 1000

    async def regenerate(
        self,
        features: ScoreFeatures,
        weights: ScoreWeights | None = None,
        tenant_id: UUID | None = None,
    ) -> ScoreResponse:
        """Compute and persist the score, or return the stale previous one.

        ``weights`` is ``None`` when the tenant rules were not available: the score then uses the
        default weights and says so in ``rules_source`` and in a distinct ``score_version``.
        """
        lock_key = f"lock:score:{features.company_id}"
        owner_id = str(uuid4())
        acquired = await self._redis.set(lock_key, owner_id, nx=True, px=self._lock_ttl_ms)
        if not acquired:
            previous = await self._store.latest_score(features.company_id)
            previous_version = str(previous["score_version"]) if previous else "pending"
            return ScoreResponse(
                company_id=features.company_id,
                score=int(previous["score"]) if previous else 0,
                status="in_progress",
                stale=True,
                score_version=previous_version,
                rules_source=rules_source_of(previous_version),
            )
        try:
            breakdown = calculate_rule_score(
                icp_fit=features.icp_fit,
                signal_strength=features.signal_strength,
                territory_opportunity=features.territory_opportunity,
                weights=weights,
            )
            used = weights or ScoreWeights()
            rules_source: RulesSource = "default" if weights is None else "tenant"
            score_version = f"{rule_version(rules_source)}:{features.source_version}"
            await self._store.upsert_score(
                company_id=features.company_id,
                score=breakdown.score,
                is_hot=breakdown.is_hot,
                source_version=features.source_version,
                score_version=score_version,
                features=(
                    features.icp_fit,
                    features.signal_strength,
                    features.territory_opportunity,
                ),
                weights=(used.icp_fit, used.signal_strength, used.territory),
            )
            if tenant_id is None:
                _log.warning("score event for company %s has no tenant", features.company_id)
            await self._redis.xadd(
                STREAM_NAME,
                score_event_fields(
                    company_id=features.company_id,
                    tenant_id=tenant_id,
                    score=breakdown.score,
                    is_hot=breakdown.is_hot,
                    source_version=features.source_version,
                ),
            )
            return ScoreResponse(
                company_id=features.company_id,
                score=breakdown.score,
                status="computed",
                score_version=score_version,
                rules_source=rules_source,
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
