"""Nightly company segmentation batch (REQ-ML-002).

Builds firmographic features from ``datagen`` data, segments companies with
UMAP plus HDBSCAN (lazy imports from the ``ml`` extra), compares the result with
a pure-python K-Prototypes using Gower distance, and stores ``ml.feature_store``,
``ml.company_segment``, and a versioned ``ml.model_version``. Every random
choice uses a fixed seed. When the ``ml`` extra is missing, the batch falls back
to the pure-python K-Prototypes so it still runs offline. It relates to
``repository.py`` and ``datagen/builder.py``. Run it with
``python -m services.ml.app.segmentation``.
"""

import argparse
import asyncio
import hashlib
import json
import math
import random
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

SEED = 42
NUMERIC_FEATURES = (
    "employee_count",
    "signal_count",
    "mean_strength",
    "signal_types",
    "has_website",
)
CATEGORICAL_FEATURES = ("city", "sector_code")
ALGORITHM = "umap-hdbscan"
FALLBACK_ALGORITHM = "k-prototypes"

Features = dict[str, Any]
Clusterer = Callable[[Sequence[Features], int], list[int]]


def build_features(dataset: dict[str, list[dict[str, Any]]]) -> dict[str, Features]:
    """Return firmographic features keyed by company id."""
    by_company: dict[str, list[dict[str, Any]]] = {}
    for signal in dataset.get("signals", []):
        by_company.setdefault(str(signal["company_id"]), []).append(signal)
    result: dict[str, Features] = {}
    for company in dataset["companies"]:
        signals = by_company.get(str(company["id"]), [])
        strengths = [float(s["strength"]) for s in signals]
        result[str(company["id"])] = {
            "employee_count": float(company["employee_count"]),
            "signal_count": float(len(signals)),
            "mean_strength": sum(strengths) / len(strengths) if strengths else 0.0,
            "signal_types": float(len({s["signal_type"] for s in signals})),
            "has_website": 1.0 if company.get("website") else 0.0,
            "city": str(company["city"]),
            "sector_code": str(company["sector_code"]),
        }
    return result


def _ranges(rows: Sequence[Features]) -> dict[str, float]:
    out = {}
    for name in NUMERIC_FEATURES:
        values = [float(r[name]) for r in rows]
        out[name] = (max(values) - min(values)) or 1.0
    return out


def gower_distance(a: Features, b: Features, ranges: dict[str, float]) -> float:
    """Mean of range-normalized numeric gaps and categorical mismatches."""
    total = sum(abs(float(a[n]) - float(b[n])) / ranges[n] for n in NUMERIC_FEATURES)
    total += sum(0.0 if a[n] == b[n] else 1.0 for n in CATEGORICAL_FEATURES)
    return total / (len(NUMERIC_FEATURES) + len(CATEGORICAL_FEATURES))


def k_prototypes(
    rows: Sequence[Features], k: int, *, seed: int = SEED, iterations: int = 25
) -> list[int]:
    """Seeded K-Prototypes: mean for numeric, mode for categorical, Gower distance."""
    if not rows:
        return []
    k = max(1, min(k, len(rows)))
    ranges = _ranges(rows)
    rng = random.Random(seed)
    centers = [dict(rows[i]) for i in rng.sample(range(len(rows)), k)]
    labels = [-1] * len(rows)
    for _ in range(iterations):
        new = [
            min(range(k), key=lambda c, r=row: (gower_distance(r, centers[c], ranges), c))  # type: ignore[misc]
            for row in rows
        ]
        if new == labels:
            break
        labels = new
        for c in range(k):
            members = [rows[i] for i, lab in enumerate(labels) if lab == c]
            if not members:
                continue
            center: Features = {
                n: sum(float(m[n]) for m in members) / len(members) for n in NUMERIC_FEATURES
            }
            for n in CATEGORICAL_FEATURES:
                counts = Counter(m[n] for m in members)
                center[n] = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            centers[c] = center
    return labels


def umap_hdbscan(rows: Sequence[Features], seed: int) -> list[int]:
    """UMAP embedding of one-hot features followed by HDBSCAN; -1 marks noise."""
    import hdbscan  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415
    import numpy as np  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415
    import umap  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415
    from sklearn.preprocessing import StandardScaler  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415

    categories = {n: sorted({str(r[n]) for r in rows}) for n in CATEGORICAL_FEATURES}
    matrix = [
        [float(r[n]) for n in NUMERIC_FEATURES]
        + [1.0 if str(r[n]) == v else 0.0 for n in CATEGORICAL_FEATURES for v in categories[n]]
        for r in rows
    ]
    scaled = StandardScaler().fit_transform(np.array(matrix))
    embedding = umap.UMAP(
        n_neighbors=min(15, max(2, len(rows) - 1)), n_components=2, random_state=seed
    ).fit_transform(scaled)
    labels = hdbscan.HDBSCAN(min_cluster_size=max(5, len(rows) // 20)).fit_predict(embedding)
    return [int(x) for x in labels]


def default_clusterer(rows: Sequence[Features], seed: int) -> tuple[str, list[int]]:
    """Use UMAP plus HDBSCAN when the ``ml`` extra is installed, else K-Prototypes."""
    try:
        return ALGORITHM, umap_hdbscan(rows, seed)
    except ImportError:
        return FALLBACK_ALGORITHM, k_prototypes(rows, _default_k(len(rows)), seed=seed)


def _default_k(n: int) -> int:
    return max(2, min(8, round(math.sqrt(n / 2)))) if n > 1 else 1


def adjusted_rand_index(a: Sequence[int], b: Sequence[int]) -> float:
    """Agreement between two labelings (1.0 identical, about 0.0 random)."""
    n = len(a)
    if n < 2:
        return 1.0

    def comb(x: int) -> float:
        return x * (x - 1) / 2

    pairs = Counter(zip(a, b, strict=True))
    sum_ij = sum(comb(v) for v in pairs.values())
    sum_a = sum(comb(v) for v in Counter(a).values())
    sum_b = sum(comb(v) for v in Counter(b).values())
    expected = sum_a * sum_b / comb(n)
    maximum = (sum_a + sum_b) / 2
    if maximum == expected:
        return 1.0
    return (sum_ij - expected) / (maximum - expected)


def segment_name(cluster_id: int) -> str:
    return "noise" if cluster_id < 0 else f"segment-{cluster_id:02d}"


@dataclass(frozen=True)
class SegmentationResult:
    model_version: str
    algorithm: str
    metrics: dict[str, Any]
    features: dict[str, Features]
    segments: dict[str, int] = field(default_factory=dict)


def source_version(features: dict[str, Features]) -> str:
    payload = json.dumps(features, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def run_segmentation(
    dataset: dict[str, list[dict[str, Any]]],
    *,
    seed: int = SEED,
    clusterer: Clusterer | None = None,
    as_of: date | None = None,
) -> SegmentationResult:
    """Compute features, segments, and comparison metrics deterministically."""
    features = build_features(dataset)
    ids = sorted(features)
    rows = [features[i] for i in ids]
    if clusterer is None:
        algorithm, labels = default_clusterer(rows, seed)
    else:
        algorithm, labels = "custom", clusterer(rows, seed)
    baseline = k_prototypes(rows, _default_k(len(rows)), seed=seed)
    ranges = _ranges(rows) if rows else {}
    clusters = {lab for lab in labels if lab >= 0}
    metrics = {
        "seed": seed,
        "companies": len(rows),
        "clusters": len(clusters),
        "noise": sum(1 for lab in labels if lab < 0),
        "kprototypes_clusters": len(set(baseline)),
        "adjusted_rand_vs_kprototypes": round(adjusted_rand_index(labels, baseline), 4),
        "mean_gower_to_global": round(
            sum(gower_distance(r, rows[0], ranges) for r in rows) / len(rows), 4
        )
        if rows
        else 0.0,
    }
    stamp = (as_of or datetime.now(UTC).date()).strftime("%Y%m%d")
    version = f"seg-{algorithm}-{stamp}-{source_version(features)}-s{seed}"
    return SegmentationResult(
        model_version=version,
        algorithm=algorithm,
        metrics=metrics,
        features=features,
        segments=dict(zip(ids, labels, strict=True)),
    )


class SegmentStore(Protocol):
    async def save(self, result: SegmentationResult) -> None: ...


class SqlSegmentStore:
    """Write features, segments, and the model version in one transaction."""

    def __init__(self, database_url: str | None) -> None:
        from libs.common.db import create_engine  # noqa: PLC0415

        self.engine = create_engine(database_url, pool_size=2)

    async def save(self, result: SegmentationResult) -> None:
        from sqlalchemy import text  # noqa: PLC0415

        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for ml writes")
        sv = source_version(result.features)
        async with self.engine.begin() as conn:
            await conn.execute(
                text("UPDATE ml.model_version SET active = false WHERE model_type = 'segmentation'")
            )
            await conn.execute(
                text(
                    "INSERT INTO ml.model_version (version, model_type, metrics, active) "
                    "VALUES (:v, 'segmentation', CAST(:m AS jsonb), true) "
                    "ON CONFLICT (version) DO UPDATE SET metrics = EXCLUDED.metrics, active = true"
                ),
                {"v": result.model_version, "m": json.dumps(result.metrics)},
            )
            for cid, feats in result.features.items():
                await conn.execute(
                    text(
                        "INSERT INTO ml.feature_store (company_id, features, source_version) "
                        "VALUES (:c, CAST(:f AS jsonb), :s) ON CONFLICT (company_id) DO UPDATE "
                        "SET features = EXCLUDED.features, "
                        "source_version = EXCLUDED.source_version, "
                        "computed_at = now()"
                    ),
                    {"c": cid, "f": json.dumps(feats), "s": sv},
                )
                label = result.segments[cid]
                await conn.execute(
                    text(
                        "INSERT INTO ml.company_segment "
                        "(company_id, segment_name, cluster_id, model_version) "
                        "VALUES (:c, :n, :k, :v) ON CONFLICT (company_id) DO UPDATE SET "
                        "segment_name = EXCLUDED.segment_name, cluster_id = EXCLUDED.cluster_id, "
                        "model_version = EXCLUDED.model_version, assigned_at = now()"
                    ),
                    {"c": cid, "n": segment_name(label), "k": label, "v": result.model_version},
                )

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()


async def run_batch(
    store: SegmentStore, dataset: dict[str, list[dict[str, Any]]], *, seed: int = SEED
) -> SegmentationResult:
    result = run_segmentation(dataset, seed=seed)
    await store.save(result)
    return result


def main() -> int:
    from datagen.builder import build_dataset  # noqa: PLC0415
    from libs.common.settings import get_settings  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="all")
    parser.add_argument("--orgs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    dataset = build_dataset(city=args.city, orgs=args.orgs, days=90, seed=args.seed)
    store = SqlSegmentStore(get_settings().database_url)
    result = asyncio.run(run_batch(store, dataset, seed=args.seed))
    print(json.dumps({"model_version": result.model_version, **result.metrics}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
