"""Aggregate the market view of one tenant's companies and signals (REQ-STR-001, INV-01).

``read_insights`` runs three tenant-scoped reads in one transaction that calls
``set_tenant_context`` first (row level security on ``core.company`` and ``core.signal``) and
filters ``tenant_id = :tenant_id`` in every statement; ``build_insights`` turns the rows into
the response body. It serves ``GET /internal/core/companies/insights`` (``main.py``), which the
gateway exposes as ``/api/v1/companies/insights`` for the Streamlit Market page, so Streamlit
never reads the ``core`` schema. The sections are:

- ``by_city_category``: companies per city and category. The category is the one of the latest
  ``maps`` signal of the company, else the company ``sector_code``.
- ``maps_by_city``: Maps listings per city with the average ``rating`` and ``reviews_count``
  (``review_count`` is accepted too); only JSON numbers count, so a null means no data.
- ``hiring``: ``linkedin_jobs`` postings per city and keyword. The keyword is the payload
  ``keyword``, else the job ``title``; the city is the payload ``city``, else the company city.
- ``mid_size``: the share of companies with ``employee_count`` between 11 and 500 among the
  companies whose size is known, in total and per city (null when no size is known).

The read is live, so ``as_of`` is the read time and ``stale`` is false. Without a tenant the body
is empty and no statement runs. Cost is three grouped scans of one tenant.
"""

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from libs.common.tenant import set_tenant_context

MID_SIZE_MIN = 11
MID_SIZE_MAX = 500
MAX_ROWS = 200
# Per-group counters summed per city; size counters summed for the total.
_SUMS = ("listings", "rated", "rating_sum", "reviewed", "reviews_sum")
_SIZE_FIELDS = ("companies", "with_size", "mid_size")

CITY_CATEGORY_SQL = """
WITH maps AS (
    SELECT DISTINCT ON (s.company_id) s.company_id, s.payload
    FROM core.signal s
    WHERE s.tenant_id = :tenant_id AND s.source = 'maps'
    ORDER BY s.company_id, s.observed_at DESC, s.id DESC
)
SELECT c.city,
       COALESCE(NULLIF(btrim(m.payload->>'category'), ''), c.sector_code) AS category,
       count(*) AS companies,
       count(m.company_id) AS listings,
       count(*) FILTER (WHERE jsonb_typeof(m.payload->'rating') = 'number') AS rated,
       sum(CASE WHEN jsonb_typeof(m.payload->'rating') = 'number'
                THEN CAST(m.payload->>'rating' AS numeric) END) AS rating_sum,
       count(*) FILTER (
           WHERE jsonb_typeof(COALESCE(m.payload->'reviews_count', m.payload->'review_count'))
                 = 'number'
       ) AS reviewed,
       sum(CASE WHEN jsonb_typeof(COALESCE(m.payload->'reviews_count',
                                           m.payload->'review_count')) = 'number'
                THEN CAST(COALESCE(m.payload->>'reviews_count', m.payload->>'review_count')
                          AS numeric) END) AS reviews_sum
FROM core.company c
LEFT JOIN maps m ON m.company_id = c.id
WHERE c.tenant_id = :tenant_id
GROUP BY 1, 2
"""
HIRING_SQL = """
SELECT COALESCE(NULLIF(btrim(s.payload->>'city'), ''), c.city) AS city,
       lower(COALESCE(NULLIF(btrim(s.payload->>'keyword'), ''),
                      NULLIF(btrim(s.payload->>'title'), ''))) AS keyword,
       count(*) AS postings,
       count(DISTINCT s.company_id) AS companies
FROM core.signal s
JOIN core.company c ON c.id = s.company_id
WHERE s.tenant_id = :tenant_id AND s.source = 'linkedin_jobs'
GROUP BY 1, 2
ORDER BY postings DESC, 1, 2
LIMIT :limit
"""
MID_SIZE_SQL = """
SELECT city,
       count(*) AS companies,
       count(employee_count) AS with_size,
       count(*) FILTER (WHERE employee_count BETWEEN :min_employees AND :max_employees)
           AS mid_size
FROM core.company
WHERE tenant_id = :tenant_id
GROUP BY city
ORDER BY companies DESC, city
"""


def _ratio(part: float, whole: float, digits: int = 2) -> float | None:
    return round(part / whole, digits) if whole else None


def _maps_row(row: Mapping[str, Any]) -> dict[str, Any]:
    rated, reviewed = int(row["rated"] or 0), int(row["reviewed"] or 0)
    return {
        "listings": int(row["listings"] or 0),
        "avg_rating": _ratio(float(row["rating_sum"] or 0), rated),
        "avg_reviews": _ratio(float(row["reviews_sum"] or 0), reviewed, 1),
    }


def _share(companies: int, with_size: int, mid_size: int) -> dict[str, Any]:
    return {
        "companies": companies,
        "companies_with_size": with_size,
        "mid_size": mid_size,
        "share": _ratio(mid_size, with_size, 4),
    }


def build_insights(
    city_category: Iterable[Mapping[str, Any]],
    hiring: Iterable[Mapping[str, Any]],
    sizes: Iterable[Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Map the grouped rows to the response body; averages are weighted by their counts."""
    groups = sorted(
        city_category,
        key=lambda r: (-int(r["companies"]), str(r["city"] or ""), str(r["category"] or "")),
    )
    per_city: dict[str | None, dict[str, float]] = {}
    for row in groups:
        totals = per_city.setdefault(row["city"], dict.fromkeys(_SUMS, 0.0))
        for name in _SUMS:
            totals[name] += float(row[name] or 0)
    maps_by_city = [
        {"city": city, **_maps_row(totals)}
        for city, totals in sorted(per_city.items(), key=lambda kv: -kv[1]["listings"])
        if totals["listings"]
    ]
    size_rows = list(sizes)
    total = [sum(int(r[name] or 0) for r in size_rows) for name in _SIZE_FIELDS]
    return {
        "as_of": now.isoformat(),
        "stale": False,
        "companies": sum(int(r["companies"]) for r in groups),
        "by_city_category": [
            {
                "city": row["city"],
                "category": row["category"],
                "companies": int(row["companies"]),
                **_maps_row(row),
            }
            for row in groups[:MAX_ROWS]
        ],
        "maps_by_city": maps_by_city,
        "hiring": [
            {
                "city": row["city"],
                "keyword": row["keyword"],
                "postings": int(row["postings"]),
                "companies": int(row["companies"]),
            }
            for row in hiring
        ],
        "mid_size": {
            "min_employees": MID_SIZE_MIN,
            "max_employees": MID_SIZE_MAX,
            **_share(*total),
            "by_city": [
                {"city": row["city"], **_share(*(int(row[n] or 0) for n in _SIZE_FIELDS))}
                for row in size_rows
            ],
        },
    }


async def read_insights(engine: AsyncEngine, tenant_id: UUID | None) -> dict[str, Any]:
    """Return the market insights of ``tenant_id``; an empty body when no tenant is given."""
    now = datetime.now(UTC)
    if tenant_id is None:
        return build_insights([], [], [], now)
    params: dict[str, Any] = {"tenant_id": tenant_id}
    bounds = {"min_employees": MID_SIZE_MIN, "max_employees": MID_SIZE_MAX}
    async with engine.begin() as connection:
        await set_tenant_context(connection, tenant_id)
        groups = await connection.execute(text(CITY_CATEGORY_SQL), params)
        hiring = await connection.execute(text(HIRING_SQL), params | {"limit": MAX_ROWS})
        sizes = await connection.execute(text(MID_SIZE_SQL), params | bounds)
        return build_insights(_rows(groups), _rows(hiring), _rows(sizes), now)


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]
