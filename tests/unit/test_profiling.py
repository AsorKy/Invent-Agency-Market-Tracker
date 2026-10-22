"""Tests for profiling, communities, publishing, and schedule hooks (REQ-LNS-004)."""

import asyncio
from typing import Any

import pytest

from libs.common.internal_auth import verify_service_token
from libs.domain.topics import MissingDependencyError
from services.ml.app import communities, profiling
from services.ml.app.publisher import PATHS, ProfileResult, PulsePublisher
from services.ml.app.schedule import run_monthly, run_weekly

ROWS = [
    {"id": "a", "size": 1.0, "city": "x"},
    {"id": "b", "size": 2.0, "city": "x"},
    {"id": "c", "size": 9.0, "city": "y"},
]


def fake_clusterer(matrix: list[list[Any]], cat: list[int], k: int, seed: int) -> list[int]:
    assert cat == [1] and seed == 42
    return [0 if row[0] < 5 else min(1, k - 1) for row in matrix]


def test_profile_companies_with_fake_clusterer() -> None:
    got = profiling.profile_companies(
        ROWS, numeric=["size"], categorical=["city"], k=2, clusterer=fake_clusterer
    )
    assert got == {"a": 0, "b": 0, "c": 1}
    assert profiling.profile_companies([], numeric=[], categorical=[], k=2) == {}
    with pytest.raises(ValueError):
        profiling.profile_companies(ROWS, numeric=["size"], categorical=["city"], k=0)


def test_kmodes_missing_names_extra() -> None:
    try:
        import kmodes  # noqa: F401
    except ImportError:
        with pytest.raises(MissingDependencyError, match="kmodes"):
            profiling.kmodes_clusterer([[1, "x"]], [1], 1, 42)


def test_degree_centrality_hand_computed() -> None:
    # Star: a-b, a-c, a-d plus a duplicate and a self loop; n = 4.
    edges = [("a", "b"), ("a", "c"), ("a", "d"), ("b", "a"), ("c", "c")]
    assert communities.degree_centrality(edges) == {"a": 1.0, "b": 1 / 3, "c": 1 / 3, "d": 1 / 3}
    assert communities.degree_centrality([]) == {}


def test_leiden_missing_names_extra() -> None:
    try:
        import igraph  # noqa: F401
        import leidenalg  # noqa: F401
    except ImportError:
        with pytest.raises(MissingDependencyError, match="graph"):
            communities.leiden_communities([("a", "b")])


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, str]]] = []

    async def post(self, url: str, *, json: Any, headers: dict[str, str]) -> FakeResponse:
        self.calls.append((url, json, headers))
        return FakeResponse()


def test_publisher_signs_token_and_posts() -> None:
    client = FakeClient()
    asyncio.run(
        PulsePublisher(client, "s3cret", tenant_id="11111111-1111-1111-1111-111111111111").publish_profiles(
            ProfileResult(run_id="r1", assignments={"a": 0})
        )
    )
    url, body, headers = client.calls[0]
    assert url == PATHS["profiles"]
    assert body["assignments"] == {"a": 0}
    assert verify_service_token(headers["X-Service-Token"], "s3cret") == "ml"


def test_weekly_and_monthly_hooks_publish_with_fakes() -> None:
    client = FakeClient()
    pub = PulsePublisher(client, "s3cret", tenant_id="11111111-1111-1111-1111-111111111111")
    edges = [("a", "b")]
    asyncio.run(
        run_weekly(
            pub,
            run_id="w1",
            rows=ROWS,
            numeric=["size"],
            categorical=["city"],
            k=2,
            edges=edges,
            clusterer=fake_clusterer,
            community_finder=lambda e: {"a": 0, "b": 0},
        )
    )
    asyncio.run(
        run_monthly(
            pub,
            run_id="m1",
            period="2026-09",
            documents=["ventas bodega", "bodega"],
            lda=lambda docs, num_topics: [["bodega"]],
        )
    )
    assert [c[0] for c in client.calls] == [
        PATHS["profiles"],
        PATHS["communities"],
        PATHS["topics"],
    ]
    assert client.calls[1][1]["centrality"] == {"a": 1.0, "b": 1.0}
    assert client.calls[2][1]["lda_topics"] == [["bodega"]]
