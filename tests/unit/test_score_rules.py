"""Contract tests for the core score-rules endpoint (REQ-ML-001, REQ-SEC-005)."""

from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from services.core.app import main  # noqa: E402
from services.core.app.score_rules import build_rules_body, read_score_rules  # noqa: E402
from services.ml.app.core_rules import ScoreRulesBody  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

TENANT_A = uuid4()
TENANT_B = uuid4()
URL = "/internal/core/admin/score-rules"
ROWS = {TENANT_A: [("icp_fit", 0.5, True), ("territory", 0.2, False)]}


class Repo:
    async def score_rules(self, tenant_id: UUID) -> dict[str, object]:
        return build_rules_body(ROWS.get(tenant_id, []))


@pytest.fixture
def client():
    main.app.state.repository = Repo()
    yield TestClient(main.app)


def _get(client, service: str | None, tenant: UUID | None = TENANT_A):
    headers = service_headers(service) if service else {}
    if tenant:
        headers["X-Tenant-Id"] = str(tenant)
    return client.get(URL, headers=headers)


def test_rules_are_per_tenant_and_validate_against_ml_model(client) -> None:
    body = ScoreRulesBody.model_validate(_get(client, "ml").json())
    assert [(r.rule_name, r.weight, r.active) for r in body.rules] == [
        ("icp_fit", 0.5, True),
        ("territory", 0.2, False),
    ]
    assert body.version > 0
    other = _get(client, "ml", TENANT_B).json()
    assert other == {"rules": [], "version": 0}


@pytest.mark.parametrize("service", ["brief", "seed", "gateway"])
def test_other_services_are_refused(client, service: str) -> None:
    # require_service_token answers 401 for a valid token of a service outside the allow list.
    assert _get(client, service).status_code == 401


def test_unsigned_call_gets_401(client) -> None:
    assert _get(client, None).status_code == 401


def test_tenant_header_is_required(client) -> None:
    assert _get(client, "ml", None).status_code == 422


def test_version_changes_with_weights() -> None:
    first = build_rules_body([("icp_fit", 0.5, True)])["version"]
    second = build_rules_body([("icp_fit", 0.6, True)])["version"]
    assert first != second


async def test_read_runs_in_tenant_context_and_filters_by_tenant() -> None:
    calls: list[tuple[str, dict]] = []

    class Result:
        def all(self):
            return [("icp_fit", 0.5, True)]

    class Conn:
        async def execute(self, statement, params=None):
            calls.append((str(statement), params))
            return Result()

    class Begin:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    class Engine:
        def begin(self):
            return Begin()

    body = await read_score_rules(Engine(), TENANT_A)  # type: ignore[arg-type]
    assert "set_config('app.tenant_id'" in calls[0][0]
    assert "tenant_id = :tenant_id" in calls[1][0]
    assert calls[1][1] == {"tenant_id": TENANT_A}
    assert body["rules"] == [{"rule_name": "icp_fit", "weight": 0.5, "active": True}]
