"""Serve the demo score features of one tenant's companies to ``ml`` (REQ-LNS-007, REQ-SEC-005).

``ml_app`` has no grant on the ``core`` schema (INV-01), so the backfill of old score rows asks
core over the signed internal API. ``read_score_features`` runs inside one transaction that calls
``set_tenant_context`` first and filters ``tenant_id = :tenant_id`` in every statement. The
features come from ``libs/domain/score_features.py`` (the seed heuristic, a placeholder until the
real feature definition is decided). ``territory_opportunity`` uses the city counts of the whole
tenant, not of the page, so the answer does not depend on ``limit``. Paging is by company id:
``after`` is exclusive and ``next_after`` is null on the last page. Relates to ``main.py`` and
``services/ml/app/core_features.py``.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from libs.common.tenant import set_tenant_context
from libs.domain.score_features import SCORE_SOURCE_VERSION, CompanyProfile, features_of

DEFAULT_LIMIT = 200
MAX_LIMIT = 500  # a larger ``limit`` is clamped to this, not refused


class CompanyFeatures(BaseModel):
    company_id: UUID
    icp_fit: float
    signal_strength: float
    territory_opportunity: float
    source_version: str
    parent_event_id: UUID | None = None


class ScoreFeaturesPage(BaseModel):
    companies: list[CompanyFeatures]
    next_after: UUID | None = None


CITY_COUNTS_SQL = (
    "SELECT COALESCE(city, '') AS city, count(*) AS companies FROM core.company "
    "WHERE tenant_id = :tenant_id GROUP BY COALESCE(city, '')"
)
PAGE_SQL = (
    "SELECT c.id, c.city, (c.website IS NOT NULL AND c.website <> '') AS has_website, "
    "(c.nit IS NOT NULL AND c.nit <> '') AS has_nit, "
    "(SELECT count(*) FROM core.signal s "
    "WHERE s.company_id = c.id AND s.tenant_id = :tenant_id) AS signals "
    "FROM core.company c WHERE c.tenant_id = :tenant_id "
    "AND (CAST(:after AS uuid) IS NULL OR c.id > CAST(:after AS uuid)) "
    "ORDER BY c.id LIMIT :fetch"
)
# The latest signal.tagged event core wrote to its own outbox, per company of the page (T-198).
TAGGED_SQL = (
    "SELECT DISTINCT ON (payload->>'company_id') payload->>'company_id' AS company_id, event_id "
    "FROM core.outbox_event WHERE tenant_id = :tenant_id AND event_type = 'signal.tagged' "
    "AND payload->>'company_id' = ANY(CAST(:company_ids AS text[])) "
    "ORDER BY payload->>'company_id', id DESC"
)


def build_features_body(
    city_counts: dict[str, int],
    page: list[tuple[UUID, CompanyProfile]],
    limit: int,
    tagged: dict[UUID, UUID] | None = None,
) -> dict[str, Any]:
    """Map the tenant city counts and one page of ``(id, profile)`` to the response body.

    ``page`` holds at most ``limit + 1`` rows ordered by id; the extra row only says there is
    more and is not returned.
    """
    widest = max(city_counts.values(), default=1)
    tagged = tagged or {}
    shown = page[:limit]
    companies = [
        {
            "company_id": str(company_id),
            **features_of(profile, city_counts.get(profile.city or "", 1), widest),
            "source_version": SCORE_SOURCE_VERSION,
            "parent_event_id": str(tagged[company_id]) if company_id in tagged else None,
        }
        for company_id, profile in shown
    ]
    more = len(page) > limit
    return {"companies": companies, "next_after": companies[-1]["company_id"] if more else None}


async def read_score_features(
    engine: AsyncEngine, tenant_id: UUID, limit: int, after: UUID | None
) -> dict[str, Any]:
    """Return the features of ``tenant_id`` companies after ``after``, at most ``limit``."""
    limit = max(1, min(limit, MAX_LIMIT))
    async with engine.begin() as connection:
        await set_tenant_context(connection, tenant_id)
        counts = await connection.execute(text(CITY_COUNTS_SQL), {"tenant_id": tenant_id})
        city_counts = {str(r[0]): int(r[1]) for r in counts.all()}
        result = await connection.execute(
            text(PAGE_SQL),
            {"tenant_id": tenant_id, "after": after, "fetch": limit + 1},
        )
        page = [
            (
                UUID(str(r[0])),
                CompanyProfile(
                    city=r[1], has_website=bool(r[2]), has_nit=bool(r[3]), signals=int(r[4])
                ),
            )
            for r in result.all()
        ]
        shown_ids = [str(company_id) for company_id, _ in page[:limit]]
        tagged: dict[UUID, UUID] = {}
        if shown_ids:
            rows = await connection.execute(
                text(TAGGED_SQL), {"tenant_id": tenant_id, "company_ids": shown_ids}
            )
            tagged = {UUID(str(r[0])): UUID(str(r[1])) for r in rows.all()}
    return build_features_body(city_counts, page, limit, tagged)
