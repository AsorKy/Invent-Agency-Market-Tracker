"""Validation read routes: cards of page 9, meta-ml of page 7, tenant scope, thresholds (T-147)."""

from typing import Any
from uuid import UUID

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")
testclient = pytest.importorskip("fastapi.testclient")
from support_cov_services import FakeEngine, FakeResult  # noqa: E402

from services.core.app import main as core  # noqa: E402
from services.core.app import validation_reads as reads  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

TENANT = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")
PROJECT = UUID("33333333-3333-3333-3333-333333333333")
LOCAL = {"APP_PROFILE": "local", "ENVIRONMENT": "local"}
GATEWAY = service_headers("gateway", **{"X-Tenant-Id": str(TENANT)})
BASE = f"/internal/core/validation/projects/{PROJECT}"
FINANCIAL = {
    "project_id": PROJECT,
    "source_version": "validation-worker-1",
    "measured_at": "2026-10-22T00:00:00+00:00",
    "npv": 12.5,
    "irr": 0.1,
    "payback_periods": 10.0,
    "discounted_payback_periods": None,
    "emv": None,
    "horizon": "H1",
}


class Tx:
    def __init__(self, rows: dict[tuple[str, UUID, UUID], dict[str, Any]], tenant: UUID) -> None:
        self.rows, self.tenant = rows, tenant

    async def project_exists(self, project_id: UUID, tenant_id: UUID) -> bool:
        return project_id == PROJECT and tenant_id == self.tenant == TENANT

    async def latest(self, card: str, project_id: UUID, tenant_id: UUID) -> dict[str, Any] | None:
        return self.rows.get((card, project_id, tenant_id))


class Reader:
    def __init__(self, rows: dict[tuple[str, UUID, UUID], dict[str, Any]], tenant: UUID) -> None:
        self.rows, self.tenant = rows, tenant

    async def __aenter__(self) -> Tx:
        return Tx(self.rows, self.tenant)

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.fixture
def rows() -> dict[tuple[str, UUID, UUID], dict[str, Any]]:
    return {
        ("financial", PROJECT, TENANT): FINANCIAL,
        (
            "portfolio",
            PROJECT,
            TENANT,
        ): {
            "source_version": "v",
            "measured_at": "t",
            "horizon_mix": {"H1": 0.6, "H2": 0.3, "H3": 0.1},
            "hhi": 0.46,
            "sharpe_like": None,
        },
        ("meta-ml", reads.TENANT_WIDE_PROJECT, TENANT): {
            "source_version": "v",
            "measured_at": "t",
            "model_utility": None,
            "decision_latency_days": None,
            "psi": None,
            "precision_at_k": 0.5,
            "funnel_lift": 1.5,
        },
    }


@pytest.fixture
def client(rows: dict, monkeypatch: pytest.MonkeyPatch) -> Any:
    for key, value in LOCAL.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(core, "validation_reader", lambda _e, tenant: Reader(rows, tenant))

    class Repo:
        engine = None

    core.app.state.repository = Repo()
    return testclient.TestClient(core.app)


def test_financial_card_has_values_and_threshold_decision(client: Any) -> None:
    body = client.get(f"{BASE}/financial", headers=GATEWAY).json()
    assert body["card"] == "financial" and body["project_id"] == str(PROJECT)
    assert body["npv"] == 12.5 and body["empty"] is False and body["reason"] is None
    assert body["threshold_version"] == 1
    assert body["thresholds"]["irr"] == {"metric": "irr", "status": "watch"}
    assert body["thresholds"]["payback_periods"]["status"] == "healthy"  # H1, 10 < 18
    assert body["source_version"] == "validation-worker-1" and body["measured_at"]


def test_portfolio_card_has_mix_deviation(client: Any) -> None:
    body = client.get(f"{BASE}/portfolio", headers=GATEWAY).json()
    assert body["horizon_mix_deviation"] == pytest.approx(0.0)
    assert body["thresholds"]["horizon_mix_deviation"]["status"] == "healthy"


def test_card_without_row_is_empty_with_reason(client: Any) -> None:
    body = client.get(f"{BASE}/growth", headers=GATEWAY).json()
    assert body["empty"] is True and body["reason"] == "no_measurement"
    assert body["mrr"] is None and body["thresholds"] == {}


def test_meta_ml_route_and_unavailable_reasons(client: Any) -> None:
    body = client.get("/internal/core/validation/meta-ml", headers=GATEWAY).json()
    assert body["funnel_lift"] == 1.5 and body["precision_at_k"] == 0.5
    assert body["unavailable"]["psi"] == "needs_ml_schema_inv01"
    assert body["thresholds"]["psi"]["status"] == "unknown"
    per_project = client.get(f"{BASE}/meta-ml", headers=GATEWAY).json()
    assert per_project["funnel_lift"] == 1.5 and per_project["project_id"] == str(PROJECT)


def test_every_page_card_url_is_served(client: Any) -> None:
    for path in ("financial", "unit-economics", "funnel", "growth", "portfolio", "meta-ml"):
        assert client.get(f"{BASE}/{path}", headers=GATEWAY).status_code == 200


def test_unknown_card_and_project_and_other_tenant(client: Any) -> None:
    assert client.get(f"{BASE}/nope", headers=GATEWAY).status_code == 404
    missing = f"/internal/core/validation/projects/{OTHER}/financial"
    assert client.get(missing, headers=GATEWAY).json()["detail"] == "project_not_found"
    headers = service_headers("gateway", **{"X-Tenant-Id": str(OTHER)})
    assert client.get(f"{BASE}/financial", headers=headers).status_code == 404


def test_tenant_header_is_required(client: Any) -> None:
    headers = service_headers("gateway")
    assert client.get(f"{BASE}/financial", headers=headers).status_code == 400
    assert client.get("/internal/core/validation/meta-ml", headers=headers).status_code == 400


def test_only_the_gateway_is_allowed(client: Any) -> None:
    urls = [f"{BASE}/financial", "/internal/core/validation/meta-ml"]
    for service in ("ml", "brief", "ingest", "seed"):
        headers = service_headers(service, **{"X-Tenant-Id": str(TENANT)})
        assert all(client.get(u, headers=headers).status_code == 401 for u in urls)
    assert client.get(urls[0], headers={"X-Tenant-Id": str(TENANT)}).status_code == 401


def test_no_database_answers_503(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in LOCAL.items():
        monkeypatch.setenv(key, value)

    class Repo:
        engine = None

    core.app.state.repository = Repo()
    response = testclient.TestClient(core.app).get(f"{BASE}/financial", headers=GATEWAY)
    assert response.status_code == 503


async def test_sql_reader_sets_tenant_first_and_reads_only_core_and_validation() -> None:
    engine = FakeEngine([FakeResult(), FakeResult([{"id": PROJECT}]), FakeResult([FINANCIAL])])
    body = await reads.read_card(
        reads.validation_reader(engine, TENANT),  # type: ignore[arg-type]
        TENANT,
        "financial",
        PROJECT,
    )
    statements = [call[0] for call in engine.connection.calls]
    assert "set_config('app.tenant_id'" in statements[0]
    assert "FROM core.project" in statements[1]
    assert "FROM validation.financial_run" in statements[2]
    assert not any("ml." in s.replace("html", "") or " pulse." in s for s in statements)
    assert body["npv"] == 12.5


def test_decimal_and_json_string_rows_are_plain() -> None:
    from decimal import Decimal

    body = reads.build_card(
        "portfolio",
        PROJECT,
        {"horizon_mix": '{"H1": 1.0}', "hhi": Decimal("1.0"), "sharpe_like": None},
    )
    assert body["hhi"] == 1.0 and body["horizon_mix_deviation"] == pytest.approx(0.4)
