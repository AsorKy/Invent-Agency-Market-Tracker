"""Weekly and monthly entry points for D2/D3 jobs (REQ-LNS-004).

Each function runs once and returns; the external scheduler (n8n or cron) owns
the cadence: ``run_weekly`` for profiling and communities, ``run_monthly`` for
topics and LDA. Inputs and the publisher are injected so tests use fakes. No
loops are used. It relates to ``profiling.py``, ``communities.py``,
``publisher.py``, and ``libs/domain/topics.py``.
"""

from collections.abc import Callable, Sequence
from typing import Any

from libs.domain import topics
from services.ml.app import communities, profiling
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
