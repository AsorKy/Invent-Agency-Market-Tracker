"""K-Prototypes company profiling for lens D2 (REQ-LNS-004).

``profile_companies`` clusters mixed numeric and categorical rows with the
``kmodes`` package (lazy import, ``ml`` extra) using a fixed seed, and returns
one archetype label per company. A caller may inject ``clusterer`` (for tests
or the pure fallback in ``segmentation.py``). It relates to ``schedule.py``.
"""

from collections.abc import Callable, Sequence
from importlib import import_module
from typing import Any

from libs.domain.topics import MissingDependencyError

SEED = 42
Row = dict[str, Any]
Clusterer = Callable[[list[list[Any]], list[int], int, int], list[int]]


def kmodes_clusterer(
    matrix: list[list[Any]], categorical_idx: list[int], k: int, seed: int
) -> list[int]:
    """Run ``kmodes.KPrototypes`` and return labels."""
    try:
        kmodes = import_module("kmodes.kprototypes")
    except ImportError as exc:
        raise MissingDependencyError("kmodes is not installed; install the 'ml' extra") from exc
    model = kmodes.KPrototypes(n_clusters=k, init="Cao", random_state=seed)
    return [int(x) for x in model.fit_predict(matrix, categorical=categorical_idx)]


def profile_companies(
    rows: Sequence[Row],
    *,
    numeric: Sequence[str],
    categorical: Sequence[str],
    k: int,
    seed: int = SEED,
    clusterer: Clusterer = kmodes_clusterer,
) -> dict[str, int]:
    """Return ``{company_id: cluster}``; every row needs ``id`` and all feature keys."""
    if k < 1:
        raise ValueError("k must be positive")
    if not rows:
        return {}
    columns = [*numeric, *categorical]
    matrix = [[row[c] for c in columns] for row in rows]
    cat_idx = list(range(len(numeric), len(columns)))
    labels = clusterer(matrix, cat_idx, min(k, len(rows)), seed)
    return {str(row["id"]): label for row, label in zip(rows, labels, strict=True)}
