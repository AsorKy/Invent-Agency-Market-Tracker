"""Unit tests for the ml explanation route (T-151, REQ-LNS-007); fakes, no network."""

from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from services.brief.app.explanations import normalize_explanation  # noqa: E402
from services.ml.app import main as ml_main  # noqa: E402
from services.ml.app.explanations_routes import explanation_from_inputs  # noqa: E402
from services.ml.app.repository import MlRepository  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

pytestmark = pytest.mark.timeout(120)

COMPANY = uuid4()
INPUTS: dict[str, Any] = {
    "company_id": COMPANY,
    "score": 60,
    "score_version": "rule-f1:v1",
    "feature_icp_fit": 0.9,
    "feature_signal_strength": 0.4,
    "feature_territory_opportunity": 0.5,
    "weight_icp_fit": 45.0,
    "weight_signal_strength": 35.0,
    "weight_territory": 20.0,
}


class FakeRepo:
    def __init__(self, inputs: dict[str, Any] | None, error: bool = False) -> None:
        self.inputs = inputs
        self.error = error
        self.asked: list[UUID] = []

    async def score_inputs(self, company_id: UUID) -> dict[str, Any] | None:
        if self.error:
            raise RuntimeError("down")
        self.asked.append(company_id)
        return self.inputs


def _client(repo: FakeRepo) -> TestClient:
    ml_main.app.state.repository = repo
    return TestClient(ml_main.app)


URL = f"/internal/ml/explanations/{COMPANY}"


def test_payload_matches_brief_parser() -> None:
    body = _client(FakeRepo(INPUTS)).get(URL, headers=service_headers("brief")).json()
    assert body["method"] == "linear"
    assert body["drivers"][0]["feature"] == "icp_fit"
    assert body["drivers"][0]["direction"] == "up"
    assert body["drivers"][0]["contribution"] == pytest.approx(45.0 * 0.4)
    assert "found" in body["counterfactual"]
    parsed = normalize_explanation(body)
    assert parsed is not None and parsed["drivers"] and parsed["counterfactual"]


def test_weights_are_normalized_to_score_points() -> None:
    payload = explanation_from_inputs(
        {**INPUTS, "weight_icp_fit": 90.0, "weight_signal_strength": 70.0, "weight_territory": 40.0}
    )
    assert payload["score"] == pytest.approx(0.9 * 45 + 0.4 * 35 + 0.5 * 20)


def test_404_without_stored_features() -> None:
    assert _client(FakeRepo(None)).get(URL, headers=service_headers("brief")).status_code == 404


def test_503_when_database_missing() -> None:
    res = _client(FakeRepo(None, error=True)).get(URL, headers=service_headers("brief"))
    assert res.status_code == 503


@pytest.mark.parametrize("service", ["gateway", "core", "ml"])
def test_only_brief_is_allowed(service: str) -> None:
    repo = FakeRepo(INPUTS)
    assert _client(repo).get(URL, headers=service_headers(service)).status_code == 401
    assert repo.asked == []


def test_missing_token_is_refused() -> None:
    assert _client(FakeRepo(INPUTS)).get(URL).status_code == 401


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, Any] | None:
        return self.row


class _Conn:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *_a: object) -> None:
        return None

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self.sql.append(str(statement))
        return _Result(INPUTS)


class _Engine:
    def __init__(self) -> None:
        self.conn = _Conn()

    def connect(self) -> _Conn:
        return self.conn


async def test_repository_read_only_touches_ml_schema() -> None:
    repo = MlRepository(None)
    repo.engine = _Engine()  # type: ignore[assignment]
    assert await repo.score_inputs(COMPANY) == INPUTS
    sql = _Engine().conn.sql or repo.engine.conn.sql  # type: ignore[attr-defined]
    assert "ml.lead_score" in sql[0]
    assert "core." not in sql[0] and "pulse." not in sql[0]
