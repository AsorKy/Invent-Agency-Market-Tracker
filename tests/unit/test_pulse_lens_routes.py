"""Unit tests for the Pulse lens routes (T-135, REQ-LNS-003, REQ-LNS-004).

The store is replaced by an in-memory fake, so no database is used. Covers the
ml ingest paths of ``services/ml/app/publisher.py``, the service allow list,
tenant resolution, the project metrics with hand-computed values, and the
socioeconomic refresh against a temporary indicator directory.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")
testclient = pytest.importorskip("fastapi.testclient")

from libs.domain.socio import INDICATOR_FIELDS  # noqa: E402
from services.ml.app.publisher import PATHS, CommunityResult, ProfileResult, TopicResult  # noqa: E402
from services.pulse.app import main as pulse  # noqa: E402
from services.pulse.app.descriptive import ProjectRow, StageVisit  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
ML = service_headers("ml", **{"X-Tenant-Id": TENANT})


class FakeStore:
    """Record what the routes store and return canned project rows."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, UUID, Any]] = []

    async def save_profiles(self, tenant: UUID, body: Any) -> int:
        self.saved.append(("profiles", tenant, body))
        return len(body.assignments)

    async def save_communities(self, tenant: UUID, body: Any) -> int:
        self.saved.append(("communities", tenant, body))
        return len(body.communities)

    async def save_topics(self, tenant: UUID, body: Any) -> int:
        self.saved.append(("topics", tenant, body))
        return len(body.top_terms) + len(body.lda_topics)

    async def project_inputs(self, tenant: UUID) -> Any:
        self.saved.append(("metrics", tenant, None))
        projects = [
            ProjectRow("a", "new", "saas", 0.5, 100.0, 10.0, 12.0),
            ProjectRow("b", "new", "data", 0.7, 300.0, 20.0, 18.0),
            ProjectRow("c", "won", "saas", None, None, None, None),
        ]
        day = datetime(2026, 1, 1, tzinfo=UTC)
        visits = [
            StageVisit("a", "new", day, datetime(2026, 1, 3, tzinfo=UTC)),
            StageVisit("b", "new", day, datetime(2026, 1, 5, tzinfo=UTC)),
            StageVisit("c", "won", day, None),
        ]
        return projects, visits, datetime(2026, 1, 9, tzinfo=UTC)


@pytest.fixture
def store() -> FakeStore:
    fake = FakeStore()
    pulse.app.state.lens_store = fake
    return fake


def _client() -> Any:
    return testclient.TestClient(pulse.app)


def test_ingest_paths_match_the_ml_publisher(store: FakeStore) -> None:
    payloads = {
        "profiles": ProfileResult(run_id="r1", assignments={"c1": 0, "c2": 1}),
        "communities": CommunityResult(
            run_id="r1", communities={"n1": 0, "n2": 0, "n3": 1}, centrality={"n1": 0.5}
        ),
        "topics": TopicResult(
            run_id="r1", period="2026-10", top_terms=[[("erp", 0.9)]], lda_topics=[["a"], ["b"]]
        ),
    }
    expected = {"profiles": 2, "communities": 3, "topics": 3}
    for kind, model in payloads.items():
        response = _client().post(PATHS[kind], json=model.model_dump(), headers=ML)
        assert response.status_code == 200, response.text
        assert response.json() == {"run_id": "r1", "accepted": expected[kind]}
    assert [(k, str(t)) for k, t, _ in store.saved] == [(k, TENANT) for k in payloads]


def test_ingest_accepts_only_the_ml_service(store: FakeStore) -> None:
    body = {"run_id": "r1", "assignments": {"c1": 0}}
    path = PATHS["profiles"]
    for headers in (service_headers("gateway", **{"X-Tenant-Id": TENANT}), {}):
        assert _client().post(path, json=body, headers=headers).status_code == 401
    assert store.saved == []


def test_ingest_requires_a_consistent_tenant(store: FakeStore) -> None:
    body = {"run_id": "r1", "assignments": {"c1": 0}}
    path = PATHS["profiles"]
    no_tenant = service_headers("ml")
    assert _client().post(path, json=body, headers=no_tenant).json()["detail"] == "tenant_required"
    from_body = _client().post(path, json={**body, "tenant_id": OTHER}, headers=no_tenant)
    assert from_body.status_code == 200
    assert str(store.saved[0][1]) == OTHER
    clash = _client().post(path, json={**body, "tenant_id": OTHER}, headers=ML)
    assert clash.status_code == 400
    assert clash.json()["detail"] == "tenant_mismatch"


def test_ingest_rejects_a_malformed_payload(store: FakeStore) -> None:
    response = _client().post(PATHS["profiles"], json={"run_id": "r1"}, headers=ML)
    assert response.status_code == 422


def test_project_metrics_are_hand_computed(store: FakeStore) -> None:
    response = _client().get(
        "/internal/pulse/projects/metrics", headers=service_headers(**{"X-Tenant-Id": TENANT})
    )
    body = response.json()
    assert response.status_code == 200
    assert body["pipeline_size"] == {"new": 2, "won": 1, "total": 3}
    assert body["score_distribution"]["n"] == 2
    assert body["score_distribution"]["p50"] == pytest.approx(0.6)
    # planned 30, actual 30 over the two projects that report both
    assert body["burn_versus_plan"] == {
        "n": 2,
        "planned": 30.0,
        "actual": 30.0,
        "variance": 0.0,
        "ratio": 1.0,
    }
    lines = {row["service_line"]: row for row in body["value_by_service_line"]}
    assert lines["data"]["share"] == pytest.approx(0.75)
    assert lines["saas"]["value"] == 100.0
    # new: closed visits of 2 and 4 days -> median 3; the open won visit is ignored
    assert body["median_days_per_stage"] == [{"stage": "new", "median_days": 3.0, "n": 2}]
    assert body["as_of"] == int(datetime(2026, 1, 9, tzinfo=UTC).timestamp() * 1000)
    assert str(store.saved[0][1]) == TENANT


def test_project_metrics_need_a_tenant_and_a_token(store: FakeStore) -> None:
    path = "/internal/pulse/projects/metrics"
    assert _client().get(path).status_code == 401
    assert _client().get(path, headers=service_headers()).status_code == 400


SOCIO_HEADERS = service_headers("n8n_socioeco")


def test_socio_refresh_counts_records(tmp_path: Path, store: FakeStore) -> None:
    header = "city,territory_code,territory_name," + ",".join(INDICATOR_FIELDS) + "\n"
    row = "Bogota,001,Chapinero," + ",".join("5" for _ in INDICATOR_FIELDS) + "\n"
    (tmp_path / "dane.csv").write_text(header + row, encoding="utf-8")
    pulse.app.state.repository = type("R", (), {"socio_data_dir": str(tmp_path)})()
    response = _client().post(
        "/internal/pulse/socioeconomic/refresh", json={"source": "dane"}, headers=SOCIO_HEADERS
    )
    assert response.status_code == 200, response.text
    assert response.json()["records"] == 1
    assert response.json()["status"] == "refreshed"


def test_socio_refresh_reports_invalid_data_and_missing_directory(
    tmp_path: Path, store: FakeStore
) -> None:
    path = "/internal/pulse/socioeconomic/refresh"
    (tmp_path / "bad.csv").write_text("city\nBogota\n", encoding="utf-8")
    pulse.app.state.repository = type("R", (), {"socio_data_dir": str(tmp_path)})()
    assert _client().post(path, json={}, headers=SOCIO_HEADERS).status_code == 503
    pulse.app.state.repository = type("R", (), {"socio_data_dir": str(tmp_path / "none")})()
    empty = _client().post(path, json={"source": "dane"}, headers=SOCIO_HEADERS)
    assert empty.json()["records"] == 0
    assert empty.json()["as_of"] is None
    assert _client().post(path, json={"source": "BAD!"}, headers=SOCIO_HEADERS).status_code == 422
    assert _client().post(path, json={}).status_code == 401
