"""Tests for the segmentation batch (REQ-ML-002) without the ml extra."""

import asyncio
import sys
from datetime import date

import pytest

from datagen.builder import build_dataset
from services.ml.app import segmentation as seg


def _dataset():
    return build_dataset(city="all", orgs=60, days=30, seed=7)


def test_features_cover_every_company():
    data = _dataset()
    feats = seg.build_features(data)
    assert len(feats) == 60
    row = next(iter(feats.values()))
    assert set(row) == set(seg.NUMERIC_FEATURES) | set(seg.CATEGORICAL_FEATURES)
    assert row["signal_count"] == 7.0


def test_kprototypes_deterministic_and_seed_sensitive():
    rows = list(seg.build_features(_dataset()).values())
    a = seg.k_prototypes(rows, 4, seed=1)
    assert a == seg.k_prototypes(rows, 4, seed=1)
    assert set(a) <= set(range(4))
    assert len(a) == len(rows)


def test_run_segmentation_with_injected_clusterer(monkeypatch):
    def fake(rows, seed):
        return [i % 3 if i % 10 else -1 for i in range(len(rows))]

    result = seg.run_segmentation(_dataset(), clusterer=fake, as_of=date(2026, 1, 1))
    again = seg.run_segmentation(_dataset(), clusterer=fake, as_of=date(2026, 1, 1))
    assert result == again
    assert result.model_version.startswith("seg-custom-20260101-")
    assert result.metrics["noise"] == 6
    assert result.metrics["clusters"] == 3
    assert seg.segment_name(-1) == "noise"


def test_fallback_without_ml_extra(monkeypatch):
    for name in ("hdbscan", "umap", "numpy"):
        monkeypatch.setitem(sys.modules, name, None)
    result = seg.run_segmentation(_dataset(), as_of=date(2026, 1, 1))
    assert result.algorithm == seg.FALLBACK_ALGORITHM
    assert result.metrics["adjusted_rand_vs_kprototypes"] == 1.0


def test_adjusted_rand_identical_and_permuted():
    assert seg.adjusted_rand_index([0, 0, 1, 1], [5, 5, 2, 2]) == 1.0
    assert seg.adjusted_rand_index([0, 1, 0, 1], [0, 0, 1, 1]) < 0.5


def test_batch_persists_through_store():
    class Memory:
        saved = None

        async def save(self, result):
            self.saved = result

    store = Memory()
    result = asyncio.run(seg.run_batch(store, _dataset()))
    assert store.saved is result
    assert set(result.segments) == set(result.features)


def test_umap_hdbscan_when_installed():
    pytest.importorskip("numpy")
    pytest.importorskip("umap")
    pytest.importorskip("hdbscan")
    pytest.importorskip("sklearn")
    rows = list(seg.build_features(build_dataset(city="all", orgs=120, days=30, seed=7)).values())
    labels = seg.umap_hdbscan(rows, seg.SEED)
    assert labels == seg.umap_hdbscan(rows, seg.SEED)
