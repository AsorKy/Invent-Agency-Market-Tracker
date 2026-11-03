"""Datasets of ``ml`` for the read-only data explorer (``libs/common/data_explorer.py``).

``ml.lead_score`` has no tenant column and no row level security: it is keyed by company id
only. ``ml_app`` may not read ``core`` (INV-01), so ``tenant_company_scope`` asks core over its
signed internal API (``core_features.HttpCoreFeatures``, the same route the backfill uses) for
the ids of the tenant's companies and the statements filter
``company_id = ANY(:company_ids)``. A row of another tenant's company is never returned; when
core cannot answer the page is a 503 and the catalog count is null. Mounted by ``main.py``.
"""

from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import text

from libs.common.data_explorer import Dataset, ScopeUnavailable, data_router
from services.ml.app.core_features import CoreFeatures, CoreFeaturesUnavailable

CORE_PAGE_SIZE = 500  # core clamps a larger page to this
MAX_CORE_PAGES = 200


async def tenant_company_scope(request: Request, tenant_id: UUID) -> dict[str, Any] | None:
    """Bind ``:company_ids``, the ids of the tenant's companies as core lists them."""
    features: CoreFeatures | None = getattr(request.app.state, "core_features", None)
    if features is None:
        raise ScopeUnavailable("no core client")
    company_ids: list[UUID] = []
    after: UUID | None = None
    for _page in range(MAX_CORE_PAGES):
        try:
            page = await features.get_page(tenant_id, CORE_PAGE_SIZE, after)
        except CoreFeaturesUnavailable as exc:
            raise ScopeUnavailable("core did not list the tenant companies") from exc
        company_ids.extend(company.company_id for company in page.companies)
        if page.next_after is None:
            return {"company_ids": company_ids}
        after = page.next_after
    raise ScopeUnavailable("the tenant has more companies than the explorer reads")


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        id="lead_scores",
        columns=(
            ("company_id", "uuid"),
            ("score", "integer"),
            ("is_hot", "boolean"),
            ("stale", "boolean"),
            ("source_version", "text"),
            ("score_version", "text"),
            ("model_version", "text"),
            ("as_of", "timestamptz"),
            ("feature_icp_fit", "numeric"),
            ("feature_signal_strength", "numeric"),
            ("feature_territory_opportunity", "numeric"),
            ("weight_icp_fit", "numeric"),
            ("weight_signal_strength", "numeric"),
            ("weight_territory", "numeric"),
        ),
        key=("as_of", "company_id"),
        page=text(
            """
            SELECT company_id, score, is_hot, stale, source_version, score_version,
                   model_version, as_of, feature_icp_fit, feature_signal_strength,
                   feature_territory_opportunity, weight_icp_fit, weight_signal_strength,
                   weight_territory
            FROM ml.lead_score
            WHERE company_id = ANY(CAST(:company_ids AS uuid[]))
              AND (CAST(:q AS text) IS NULL
                   OR strpos(company_id::text, lower(CAST(:q AS text))) > 0
                   OR strpos(lower(score_version), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(model_version), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source_version), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (as_of, company_id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS uuid)))
            ORDER BY as_of DESC, company_id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM ml.lead_score
            WHERE company_id = ANY(CAST(:company_ids AS uuid[]))
              AND (CAST(:q AS text) IS NULL
                   OR strpos(company_id::text, lower(CAST(:q AS text))) > 0
                   OR strpos(lower(score_version), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(model_version), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source_version), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
)

router = data_router("ml", DATASETS, tenant_company_scope)
