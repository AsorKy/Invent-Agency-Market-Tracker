"""Top-K and ranking metrics: lift@K, precision@K, NDCG@K, MAP, MRR.

Pure stdlib functions. Ties in scores keep input order (stable ranking).
Relates to REQ-LNS-008 and ADR-IMT-0002 section 2.3.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence

from libs.domain.metrics import _validate as v


def _top_k_hits(y_true: Sequence[int], y_score: Sequence[float], k: int) -> int:
    v.require_non_empty("y_true", y_true)
    v.require_same_length(y_true=y_true, y_score=y_score)
    v.require_binary("y_true", y_true)
    v.require_k(k, len(y_true))
    return sum(y_true[index] for index in v.ranked_order(y_score)[:k])


def precision_at_k(y_true: Sequence[int], y_score: Sequence[float], k: int) -> float:
    """Return the share of positives among the K highest-scored items."""
    return _top_k_hits(y_true, y_score, k) / k


def lift_at_k(y_true: Sequence[int], y_score: Sequence[float], k: int) -> float:
    """Return precision@K divided by the overall positive rate.

    1.0 means no better than random. Raises ``ValueError`` when there are no
    positives, because the base rate is zero.
    """
    hits = _top_k_hits(y_true, y_score, k)
    base_rate = sum(y_true) / len(y_true)
    if base_rate == 0:
        raise ValueError("y_true must contain at least one positive to compute lift")
    return (hits / k) / base_rate


def precision_at_k_by_group(
    y_true: Sequence[int],
    y_score: Sequence[float],
    groups: Sequence[Hashable],
    k: int,
) -> dict[Hashable, float]:
    """Return precision@K computed separately inside each subpopulation.

    A group with fewer than ``k`` items uses all of its items (precision over
    the items actually available). Never report a global number alone.
    """
    v.require_non_empty("y_true", y_true)
    v.require_same_length(y_true=y_true, y_score=y_score, groups=groups)
    v.require_binary("y_true", y_true)
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(f"k must be a positive integer, got {k!r}")
    members: dict[Hashable, list[int]] = {}
    for index, group in enumerate(groups):
        members.setdefault(group, []).append(index)
    result: dict[Hashable, float] = {}
    for group, indices in members.items():
        ordered = sorted(indices, key=lambda index: -y_score[index])[:k]
        result[group] = sum(y_true[index] for index in ordered) / len(ordered)
    return result


def ndcg_at_k(relevance: Sequence[float], y_score: Sequence[float], k: int) -> float:
    """Return NDCG@K with linear gain and a 1/log2(rank + 1) discount.

    ``relevance`` holds graded gains (non-negative). Returns 0.0 when no item
    has positive relevance (the ideal DCG is zero).
    """
    v.require_non_empty("relevance", relevance)
    v.require_same_length(relevance=relevance, y_score=y_score)
    v.require_k(k, len(relevance))
    if any(gain < 0 for gain in relevance):
        raise ValueError("relevance must be non-negative")

    def dcg(gains: Sequence[float]) -> float:
        return sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains[:k]))

    ranked = [relevance[index] for index in v.ranked_order(y_score)]
    ideal = dcg(sorted(relevance, reverse=True))
    return 0.0 if ideal == 0 else dcg(ranked) / ideal


def _check_queries(name: str, queries: Sequence[Sequence[int]]) -> None:
    v.require_non_empty(name, queries)
    for query in queries:
        v.require_non_empty(f"each query in {name}", query)
        v.require_binary(name, query)


def mean_average_precision(ranked_relevance: Sequence[Sequence[int]]) -> float:
    """Return MAP over queries.

    Each query is a 0/1 relevance list already in ranked order (best first).
    Average precision is the mean of precision at each relevant rank; a query
    with no relevant item scores 0.
    """
    _check_queries("ranked_relevance", ranked_relevance)
    total = 0.0
    for query in ranked_relevance:
        hits = 0
        precisions = 0.0
        for rank, relevant in enumerate(query, start=1):
            if relevant:
                hits += 1
                precisions += hits / rank
        total += precisions / hits if hits else 0.0
    return total / len(ranked_relevance)


def mean_reciprocal_rank(ranked_relevance: Sequence[Sequence[int]]) -> float:
    """Return MRR: mean of 1 / rank of the first relevant item (0 if none)."""
    _check_queries("ranked_relevance", ranked_relevance)
    total = 0.0
    for query in ranked_relevance:
        for rank, relevant in enumerate(query, start=1):
            if relevant:
                total += 1.0 / rank
                break
    return total / len(ranked_relevance)
