"""Persist the current score and history in the ``ml`` schema.

``upsert_score`` updates the company read model and appends a history row;
``latest_score`` returns the current version for queries and for responses when
another request holds the lock. It relates to ``services/ml/app/main.py``,
``consumer.py``, and the ML migration. It separates the scoring algorithm from
storage.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from libs.common.db import create_engine
from libs.domain.scoring import ScoreWeights, resolve_weights
from services.ml.app.core_rules import CoreRules, CoreRulesUnavailable

_log = logging.getLogger(__name__)


class MlRepository:
    def __init__(self, database_url: str | None, core_rules: CoreRules | None = None) -> None:
        self.engine = create_engine(database_url, pool_size=2)
        self._core_rules = core_rules

    async def close(self) -> None:
        if self._core_rules:
            await self._core_rules.close()
        if self.engine:
            await self.engine.dispose()

    async def upsert_score(
        self,
        *,
        company_id: UUID,
        score: int,
        is_hot: bool,
        source_version: str,
        score_version: str,
    ) -> None:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for ml writes")
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ml.lead_score
                        (company_id, score, is_hot, stale, source_version,
                         score_version, model_version, as_of)
                    VALUES (:company_id, :score, :is_hot, false, :source_version,
                            :score_version, 'rule-f1', :as_of)
                    ON CONFLICT (company_id) DO UPDATE SET
                        score = EXCLUDED.score,
                        is_hot = EXCLUDED.is_hot,
                        stale = false,
                        source_version = EXCLUDED.source_version,
                        score_version = EXCLUDED.score_version,
                        model_version = EXCLUDED.model_version,
                        as_of = EXCLUDED.as_of
                    """
                ),
                {
                    "company_id": company_id,
                    "score": score,
                    "is_hot": is_hot,
                    "source_version": source_version,
                    "score_version": score_version,
                    "as_of": datetime.now(UTC),
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO ml.score_history (company_id, score, source_version, model_version)
                    VALUES (:company_id, :score, :source_version, 'rule-f1')
                    """
                ),
                {"company_id": company_id, "score": score, "source_version": source_version},
            )

    async def latest_score(self, company_id: UUID) -> dict[str, object] | None:
        if self.engine is None:
            return None
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT company_id, score, stale, score_version, source_version
                        FROM ml.lead_score WHERE company_id = :company_id
                        """
                        ),
                        {"company_id": company_id},
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def mark_stale(self, company_id: UUID) -> None:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for ml writes")
        async with self.engine.begin() as connection:
            await connection.execute(
                text("UPDATE ml.lead_score SET stale = true WHERE company_id = :company_id"),
                {"company_id": company_id},
            )

    async def load_weights(self, tenant_id: UUID) -> ScoreWeights | None:
        """Resolve the tenant weights from core; ``None`` when core cannot answer.

        ``None`` makes the caller score with the default weights and report ``rules_source``
        ``default`` instead of answering HTTP 500 (the ``ml_app`` role has no grant on the
        ``core`` schema, INV-01, so the rules come over core's internal API).
        """
        if self._core_rules is None:
            return None
        try:
            rows = await self._core_rules.get_rules(tenant_id)
        except CoreRulesUnavailable as exc:
            _log.warning("tenant score rules unavailable, using default weights: %s", exc)
            return None
        return resolve_weights(rows)
