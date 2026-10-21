"""Classification and calibration metrics: AUC, PR-AUC, Brier, reliability.

Pure stdlib functions over ``y_true`` (0/1 labels) and ``y_score`` (scores or
probabilities). Relates to REQ-LNS-008 and ADR-IMT-0002 section 2.3.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from libs.domain.metrics import _validate as v


def _check_labels_scores(y_true: Sequence[int], y_score: Sequence[float]) -> None:
    v.require_non_empty("y_true", y_true)
    v.require_same_length(y_true=y_true, y_score=y_score)
    v.require_binary("y_true", y_true)


def roc_auc(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    """Return the area under the ROC curve.

    Computed as the Mann-Whitney statistic: the probability that a random
    positive outranks a random negative, counting ties as one half.
    """
    _check_labels_scores(y_true, y_score)
    v.require_both_classes("y_true", y_true)
    order = sorted(range(len(y_score)), key=lambda index: y_score[index])
    rank_sum_positive = 0.0
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and y_score[order[end + 1]] == y_score[order[start]]:
            end += 1
        average_rank = (start + end) / 2.0 + 1.0
        for position in range(start, end + 1):
            if y_true[order[position]] == 1:
                rank_sum_positive += average_rank
        start = end + 1
    positives = sum(y_true)
    negatives = len(y_true) - positives
    return (rank_sum_positive - positives * (positives + 1) / 2.0) / (positives * negatives)


def pr_auc(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    """Return average precision: sum of (recall step * precision) per threshold.

    Tied scores form one threshold, so the result does not depend on input order.
    """
    _check_labels_scores(y_true, y_score)
    positives = sum(y_true)
    if positives == 0:
        raise ValueError("y_true must contain at least one positive")
    order = v.ranked_order(y_score)
    total = 0.0
    seen = 0
    true_positives = 0
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and y_score[order[end + 1]] == y_score[order[start]]:
            end += 1
        group_positives = sum(y_true[order[position]] for position in range(start, end + 1))
        seen += end - start + 1
        true_positives += group_positives
        total += (group_positives / positives) * (true_positives / seen)
        start = end + 1
    return total


def brier_score(y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    """Return the mean squared error between probabilities and 0/1 outcomes."""
    _check_labels_scores(y_true, y_prob)
    v.require_probabilities("y_prob", y_prob)
    return sum((p - y) ** 2 for p, y in zip(y_prob, y_true, strict=True)) / len(y_true)


class ReliabilityBin(NamedTuple):
    """One non-empty bin of a reliability table."""

    lower: float
    upper: float
    n_items: int
    mean_predicted: float
    observed_rate: float


def reliability_table(
    y_true: Sequence[int], y_prob: Sequence[float], n_bins: int = 10
) -> list[ReliabilityBin]:
    """Return reliability bins over equal-width probability intervals.

    Bins are [lower, upper) except the last, which includes 1.0. Empty bins are
    omitted. A calibrated model has ``mean_predicted`` close to ``observed_rate``.
    """
    _check_labels_scores(y_true, y_prob)
    v.require_probabilities("y_prob", y_prob)
    if isinstance(n_bins, bool) or not isinstance(n_bins, int) or n_bins < 1:
        raise ValueError(f"n_bins must be a positive integer, got {n_bins!r}")
    members: list[list[int]] = [[] for _ in range(n_bins)]
    for index, probability in enumerate(y_prob):
        members[min(int(probability * n_bins), n_bins - 1)].append(index)
    table = []
    for bin_index, indices in enumerate(members):
        if not indices:
            continue
        table.append(
            ReliabilityBin(
                lower=bin_index / n_bins,
                upper=(bin_index + 1) / n_bins,
                n_items=len(indices),
                mean_predicted=sum(y_prob[i] for i in indices) / len(indices),
                observed_rate=sum(y_true[i] for i in indices) / len(indices),
            )
        )
    return table
