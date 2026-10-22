"""Weekly, monthly, and nightly entry points for D2/D3 jobs (REQ-LNS-004, REQ-LNS-007).

``run_weekly`` (profiling and communities) and ``run_monthly`` (topics and LDA)
run once and return; the external scheduler (n8n or cron) owns their cadence.
``run_nightly`` is the drift loop: it waits until the next run hour, runs the
drift worker, and stops after ``max_runs`` runs (``None`` means forever, only
the ``main`` entry point does that). The clock and ``sleep`` are injected so
tests never wait. ``python -m services.ml.app.schedule`` starts the loop with
real clients. Inputs and the publisher are injected so tests use fakes. It
relates to ``profiling.py``, ``communities.py``, ``drift_worker.py``,
``publisher.py``, and ``libs/domain/topics.py``.
"""

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from libs.common.settings import get_settings
from libs.domain import topics
from services.ml.app import communities, drift_worker, profiling
from services.ml.app.publisher import (
    CommunityResult,
    ProfileResult,
    PulsePublisher,
    TopicResult,
)


async def run_weekly(
    publisher: PulsePublisher,
    *,
    run_id: str,
    rows: Sequence[dict[str, Any]],
    numeric: Sequence[str],
    categorical: Sequence[str],
    k: int,
    edges: Sequence[communities.Edge],
    clusterer: profiling.Clusterer = profiling.kmodes_clusterer,
    community_finder: Callable[..., dict[str, int]] = communities.leiden_communities,
) -> None:
    """Profile companies, detect communities, and publish both."""
    assignments = profiling.profile_companies(
        rows, numeric=numeric, categorical=categorical, k=k, clusterer=clusterer
    )
    await publisher.publish_profiles(ProfileResult(run_id=run_id, assignments=assignments))
    await publisher.publish_communities(
        CommunityResult(
            run_id=run_id,
            communities=community_finder(edges),
            centrality=communities.degree_centrality(edges),
        )
    )


async def run_monthly(
    publisher: PulsePublisher,
    *,
    run_id: str,
    period: str,
    documents: Sequence[str],
    num_topics: int = 5,
    lda: Callable[..., list[list[str]]] = topics.monthly_lda,
) -> None:
    """Compute TF-IDF top terms and LDA topics for ``period`` and publish them."""
    await publisher.publish_topics(
        TopicResult(
            run_id=run_id,
            period=period,
            top_terms=topics.tfidf_top_terms(documents),
            lda_topics=lda(documents, num_topics=num_topics),
        )
    )


logger = logging.getLogger(__name__)
NIGHTLY_HOUR_ENV = "DRIFT_RUN_HOUR_UTC"
DEFAULT_RUN_HOUR_UTC = 7
HTTP_TIMEOUT_SECONDS = 15.0


def seconds_until_hour(now: datetime, hour: int) -> float:
    """Return the seconds from ``now`` to the next ``hour``:00 UTC (never 0)."""
    target = now.astimezone(UTC).replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_nightly(
    loader: drift_worker.FeatureLoader,
    client: drift_worker.AlertClient,
    *,
    hour: int = DEFAULT_RUN_HOUR_UTC,
    max_runs: int | None = 1,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    run_id: Callable[[], str] = lambda: f"drift-{uuid.uuid4().hex[:12]}",
) -> int:
    """Run the drift worker nightly; return the number of runs started.

    A failed run is logged and the loop goes on; ``max_runs`` bounds the loop.
    """
    runs = 0
    while max_runs is None or runs < max_runs:
        await sleep(seconds_until_hour(now(), hour))
        runs += 1
        try:
            await drift_worker.run_drift(loader, client, run_id=run_id())
        except Exception:
            logger.exception("nightly drift run failed")
    return runs


async def main() -> None:
    """Start the nightly drift loop with real pulse and webhook clients."""
    settings = get_settings()
    hour = int(os.environ.get(NIGHTLY_HOUR_ENV, DEFAULT_RUN_HOUR_UTC))
    async with (
        httpx.AsyncClient(base_url=settings.pulse_url, timeout=HTTP_TIMEOUT_SECONDS) as pulse,
        httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as webhook,
    ):
        loader = drift_worker.PulseSeriesLoader(
            pulse, settings.internal_service_token, drift_worker.tenant_id()
        )
        await run_nightly(loader, webhook, hour=hour, max_runs=None)


if __name__ == "__main__":
    asyncio.run(main())
