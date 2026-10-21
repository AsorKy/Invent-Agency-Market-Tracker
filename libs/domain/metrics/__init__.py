"""Metric pack library for predictive model quality (REQ-LNS-008).

Pure, stdlib-only functions grouped by task: classification and calibration,
ranking, forecasting, and uplift. Nothing heavy is imported at package import
time. Relates to ADR-IMT-0002 section 2.3.
"""

from libs.domain.metrics.classification import (
    ReliabilityBin,
    brier_score,
    pr_auc,
    reliability_table,
    roc_auc,
)
from libs.domain.metrics.forecasting import interval_coverage, mase, pinball_loss, smape
from libs.domain.metrics.ranking import (
    lift_at_k,
    mean_average_precision,
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    precision_at_k_by_group,
)
from libs.domain.metrics.uplift import qini_coefficient, qini_curve

__all__ = [
    "ReliabilityBin",
    "brier_score",
    "interval_coverage",
    "lift_at_k",
    "mase",
    "mean_average_precision",
    "mean_reciprocal_rank",
    "ndcg_at_k",
    "pinball_loss",
    "pr_auc",
    "precision_at_k",
    "precision_at_k_by_group",
    "qini_coefficient",
    "qini_curve",
    "reliability_table",
    "roc_auc",
    "smape",
]
