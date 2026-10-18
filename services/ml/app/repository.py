"""Persist the current score and history in the ``ml`` schema.

``upsert_score`` updates the company read model and appends a history row;
``latest_score`` returns the current version for queries and for responses when
another request holds the lock. It relates to ``services/ml/app/main.py``,
``consumer.py``, and the ML migration. It separates the scoring algorithm from
storage.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from libs.common.db import create_engine
from libs.domain.scoring import ScoreWeights, resolve_weights


class MlRepository:
    def __init__(self, database_url: str | None) -> None:
        self.engine = create_engine(database_url, pool_size=2)

    async def close(self) -> None:
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

    async def load_weights(self, tenant_id: UUID) -> ScoreWeights:
        """Read ``core.lead_score_rule`` rows for a tenant; defaults without a database."""
        if self.engine is None:
            return ScoreWeights()
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT rule_name, weight, active FROM core.lead_score_rule "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": tenant_id},
                )
            ).all()
        return resolve_weights((str(r[0]), r[1], bool(r[2])) for r in rows)
