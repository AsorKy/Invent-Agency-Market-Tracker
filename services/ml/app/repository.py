"""Persist the current score and history in the ``ml`` schema.

``upsert_score`` updates the company read model and appends a history row;
``latest_score`` returns the current version for queries and for responses when
another request holds the lock. It relates to ``services/ml/app/main.py``,
``consumer.py``, and the ML migration. It separates the scoring algorithm from
storage.
"""

import json
import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from libs.common.db import create_engine
from libs.domain.scoring import ScoreWeights, resolve_weights
from services.ml.app.core_rules import CoreRules, CoreRulesUnavailable

_log = logging.getLogger(__name__)


_INSERT_OUTBOX = text(
    """
    INSERT INTO ml.outbox_event
        (event_id, tenant_id, aggregate_type, aggregate_id, event_type, version,
         schema_version, payload, occurred_at)
    VALUES
        (:event_id, :tenant_id, :aggregate_type, :aggregate_id, :event_type, :version,
         1, CAST(:payload AS jsonb), :occurred_at)
    """
)


async def insert_outbox_events(
    connection: AsyncConnection, events: list[dict[str, Any]], now: datetime
) -> None:
    """Write events to ``ml.outbox_event`` on the caller's connection (its transaction).

    ``ml.outbox_event`` has no row level security (infrastructure, D-29); ``tenant_id`` is the
    routing metadata the relay copies into the stream entry.
    """
    for event in events:
        await connection.execute(
            _INSERT_OUTBOX,
            {
                "event_id": UUID(str(event["event_id"])),
                "tenant_id": event["tenant_id"],
                "aggregate_type": event["aggregate_type"],
                "aggregate_id": event["aggregate_id"],
                "event_type": event["event_type"],
                "version": int(event["version"]),
                "payload": json.dumps(event["payload"]),
                "occurred_at": now,
            },
        )


class MlOutboxTransaction:
    """Claim, publish bookkeeping and dead lettering on ``ml.outbox_event`` (one connection)."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def claim_due(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        """Due, undelivered events; an aggregate with an earlier waiting event is held back."""
        result = await self.connection.execute(
            text(
                """
                SELECT e.id, e.event_id, e.aggregate_type, e.aggregate_id, e.event_type,
                       e.version, e.payload, e.attempts, e.replay, e.tenant_id
                FROM ml.outbox_event e
                WHERE e.published_at IS NULL AND e.dead_lettered_at IS NULL
                  AND (e.next_attempt_at IS NULL OR e.next_attempt_at <= :now)
                  AND NOT EXISTS (
                      SELECT 1 FROM ml.outbox_event p
                      WHERE p.aggregate_id = e.aggregate_id AND p.id < e.id
                        AND p.published_at IS NULL AND p.dead_lettered_at IS NULL
                        AND p.next_attempt_at > :now
                  )
                ORDER BY e.id
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"now": now, "limit": limit},
        )
        return [dict(row) for row in result.mappings().all()]

    async def mark_published(self, row_id: int, now: datetime) -> None:
        await self.connection.execute(
            text("UPDATE ml.outbox_event SET published_at = :now WHERE id = :id"),
            {"id": row_id, "now": now},
        )

    async def record_failure(
        self, row_id: int, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None:
        await self.connection.execute(
            text(
                """
                UPDATE ml.outbox_event
                SET attempts = :attempts, next_attempt_at = :next_attempt_at, last_error = :error
                WHERE id = :id
                """
            ),
            {
                "id": row_id,
                "attempts": attempts,
                "next_attempt_at": next_attempt_at,
                "error": error,
            },
        )

    async def dead_letter(
        self, row: dict[str, Any], *, attempts: int, error: str, now: datetime
    ) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO ml.outbox_dead_letter
                    (event_id, tenant_id, event_type, payload, error, attempts,
                     source_outbox_id, replay)
                VALUES
                    (:event_id, :tenant_id, :event_type, CAST(:payload AS jsonb), :error,
                     :attempts, :id, COALESCE(:replay, false))
                """
            ),
            {
                "event_id": row["event_id"],
                "tenant_id": row.get("tenant_id"),
                "event_type": row["event_type"],
                "payload": json.dumps(row["payload"], default=str),
                "error": error,
                "attempts": attempts,
                "id": row["id"],
                "replay": row.get("replay"),
            },
        )
        await self.connection.execute(
            text(
                """
                UPDATE ml.outbox_event
                SET attempts = :attempts, last_error = :error, dead_lettered_at = :now
                WHERE id = :id
                """
            ),
            {"id": row["id"], "attempts": attempts, "error": error, "now": now},
        )


class MlOutboxUnitOfWork:
    """One transaction per relay pass: commit on success, roll back on error."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._context: Any = None

    async def __aenter__(self) -> MlOutboxTransaction:
        self._context = self._engine.begin()
        return MlOutboxTransaction(await self._context.__aenter__())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._context.__aexit__(exc_type, exc, tb)


class MlRepository:
    def __init__(self, database_url: str | None, core_rules: CoreRules | None = None) -> None:
        self.engine = create_engine(database_url, pool_size=2)
        self._core_rules = core_rules

    def outbox_unit_of_work(self) -> MlOutboxUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for ml writes")
        return MlOutboxUnitOfWork(self.engine)

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
        features: tuple[float, float, float] | None = None,
        weights: tuple[float, float, float] | None = None,
        outbox_events: list[dict[str, Any]] | None = None,
    ) -> None:
        """Upsert the score; ``features`` and ``weights`` are (icp, signal, territory).

        ``outbox_events`` are written to ``ml.outbox_event`` in this same transaction, so the
        score and its events commit or roll back together (D-21).
        """
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for ml writes")
        f = features or (None, None, None)
        w = weights or (None, None, None)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ml.lead_score
                        (company_id, score, is_hot, stale, source_version,
                         score_version, model_version, as_of,
                         feature_icp_fit, feature_signal_strength,
                         feature_territory_opportunity, weight_icp_fit,
                         weight_signal_strength, weight_territory)
                    VALUES (:company_id, :score, :is_hot, false, :source_version,
                            :score_version, 'rule-f1', :as_of,
                            :f_icp, :f_signal, :f_territory, :w_icp, :w_signal, :w_territory)
                    ON CONFLICT (company_id) DO UPDATE SET
                        score = EXCLUDED.score,
                        is_hot = EXCLUDED.is_hot,
                        stale = false,
                        source_version = EXCLUDED.source_version,
                        score_version = EXCLUDED.score_version,
                        model_version = EXCLUDED.model_version,
                        as_of = EXCLUDED.as_of,
                        feature_icp_fit = EXCLUDED.feature_icp_fit,
                        feature_signal_strength = EXCLUDED.feature_signal_strength,
                        feature_territory_opportunity = EXCLUDED.feature_territory_opportunity,
                        weight_icp_fit = EXCLUDED.weight_icp_fit,
                        weight_signal_strength = EXCLUDED.weight_signal_strength,
                        weight_territory = EXCLUDED.weight_territory
                    """
                ),
                {
                    "company_id": company_id,
                    "score": score,
                    "is_hot": is_hot,
                    "source_version": source_version,
                    "score_version": score_version,
                    "as_of": datetime.now(UTC),
                    "f_icp": f[0],
                    "f_signal": f[1],
                    "f_territory": f[2],
                    "w_icp": w[0],
                    "w_signal": w[1],
                    "w_territory": w[2],
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
            if outbox_events:
                await insert_outbox_events(connection, outbox_events, datetime.now(UTC))

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

    async def score_inputs(self, company_id: UUID) -> dict[str, object] | None:
        """Stored features and weights of the current score; ``None`` when not stored.

        ``ml.lead_score`` carries no tenant column and no row level security (INV-01: only the
        ml schema is read), so there is no tenant context to set for this read.
        """
        if self.engine is None:
            return None
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT company_id, score, score_version,
                                   feature_icp_fit, feature_signal_strength,
                                   feature_territory_opportunity, weight_icp_fit,
                                   weight_signal_strength, weight_territory
                            FROM ml.lead_score
                            WHERE company_id = :company_id
                              AND feature_icp_fit IS NOT NULL
                              AND feature_signal_strength IS NOT NULL
                              AND feature_territory_opportunity IS NOT NULL
                              AND weight_icp_fit IS NOT NULL
                              AND weight_signal_strength IS NOT NULL
                              AND weight_territory IS NOT NULL
                            """
                        ),
                        {"company_id": company_id},
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def without_features(self, company_ids: list[UUID]) -> set[UUID]:
        """Subset of ``company_ids`` whose stored score row has null features.

        Ids without an ml row are not returned (nothing was ever scored for them). Reads only
        ``ml.lead_score`` (INV-01); the ids come from core's answer for one tenant.
        """
        if self.engine is None or not company_ids:
            return set()
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT company_id FROM ml.lead_score
                    WHERE company_id = ANY(CAST(:ids AS uuid[]))
                      AND (feature_icp_fit IS NULL
                           OR feature_signal_strength IS NULL
                           OR feature_territory_opportunity IS NULL)
                    """
                ),
                {"ids": company_ids},
            )
            return {UUID(str(row[0])) for row in result.all()}

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
