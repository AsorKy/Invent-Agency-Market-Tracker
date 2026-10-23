"""Backfill the features of score rows written before T-151 (REQ-LNS-007, REQ-ML-001).

Usage: ``python -m services.ml.app.backfill --tenant <uuid> --limit N`` inside the ml container.
It pages through core's ``GET /internal/core/admin/score-features`` for that tenant (ml has no
grant on the ``core`` schema, INV-01, and this module never reads it), keeps the companies whose
stored ``ml.lead_score`` row has null features (one ml read per page, restricted to the ids core
returned, so an id core did not return is never rescored), and rescores each through
``ScoringService.regenerate`` with the tenant weights, so the score version, the event and the
stored features come out as for any new score. Idempotent: a rescored row has features, so a
second run finds nothing. ``--limit`` bounds the companies rescored in one run (run again for the
rest) and ``MAX_PAGES`` bounds the paging. It prints one line of counts and never rows or tokens.
Exit code 0 on completion, 1 when core or the tenant rules are unavailable.
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from libs.common.redis_client import create_redis
from libs.common.settings import get_settings
from libs.contracts.api import ScoreFeatures
from libs.domain.scoring import ScoreWeights
from services.ml.app.core_features import CoreFeatures, CoreFeaturesUnavailable, HttpCoreFeatures
from services.ml.app.core_rules import HttpCoreRules
from services.ml.app.repository import MlRepository
from services.ml.app.scoring_service import ScoringService

PAGE_SIZE = 200
MAX_PAGES = 10_000
EXIT_UNAVAILABLE = 1


@dataclass
class BackfillCounts:
    pages: int = 0
    examined: int = 0
    missing: int = 0
    rescored: int = 0
    skipped: int = 0
    failed: int = 0

    def line(self, tenant_id: UUID) -> str:
        return (
            f"backfill tenant={tenant_id} pages={self.pages} examined={self.examined} "
            f"missing={self.missing} rescored={self.rescored} skipped={self.skipped} "
            f"failed={self.failed}"
        )


async def run_backfill(
    tenant_id: UUID,
    limit: int,
    features: CoreFeatures,
    repository: Any,
    service: ScoringService,
    weights: ScoreWeights,
    *,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> BackfillCounts:
    """Rescore at most ``limit`` companies of ``tenant_id`` whose ml row lacks features."""
    counts = BackfillCounts()
    after: UUID | None = None
    handled = 0
    while handled < limit and counts.pages < max_pages:
        page = await features.get_page(tenant_id, page_size, after)
        counts.pages += 1
        counts.examined += len(page.companies)
        by_id = {c.company_id: c for c in page.companies}
        missing = await repository.without_features(list(by_id)) & set(by_id)
        counts.missing += len(missing)
        for company in page.companies:
            if company.company_id not in missing:
                continue
            if handled >= limit:
                break
            handled += 1
            try:
                result = await service.regenerate(
                    ScoreFeatures(
                        company_id=company.company_id,
                        icp_fit=company.icp_fit,
                        signal_strength=company.signal_strength,
                        territory_opportunity=company.territory_opportunity,
                        source_version=company.source_version,
                        # Core sends the signal.tagged id on the features route when it knows it.
                        parent_event_id=getattr(company, "parent_event_id", None),
                    ),
                    weights,
                    tenant_id,
                )
            except Exception:
                counts.failed += 1
                continue
            if result.status == "computed":
                counts.rescored += 1
            else:
                counts.skipped += 1
        if page.next_after is None or not page.companies:
            break
        after = page.next_after
    return counts


async def _main(tenant_id: UUID, limit: int) -> int:
    settings = get_settings()
    token = settings.internal_service_token
    repository = MlRepository(settings.database_url, HttpCoreRules(settings.core_url, token))
    features = HttpCoreFeatures(settings.core_url, token)
    redis = create_redis(settings.redis_url)
    try:
        weights = await repository.load_weights(tenant_id)
        if weights is None:
            print("backfill stopped: tenant score rules unavailable")
            return EXIT_UNAVAILABLE
        service = ScoringService(
            repository, cast(Any, redis), lock_ttl_seconds=settings.score_lock_ttl_seconds
        )
        try:
            counts = await run_backfill(tenant_id, limit, features, repository, service, weights)
        except CoreFeaturesUnavailable:
            print("backfill stopped: core score features unavailable")
            return EXIT_UNAVAILABLE
        print(counts.line(tenant_id))
        return 0
    finally:
        await features.close()
        await repository.close()
        await redis.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill features of old score rows.")
    parser.add_argument("--tenant", type=UUID, required=True, help="tenant id (uuid)")
    parser.add_argument("--limit", type=int, required=True, help="max companies rescored")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    return asyncio.run(_main(args.tenant, args.limit))


if __name__ == "__main__":
    sys.exit(main())
