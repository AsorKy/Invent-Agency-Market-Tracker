"""Validation inputs route: typed object, ETag, tenant scope, audit, SQL shape (T-179)."""

import json
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
from services.core.app import repository as repo  # noqa: E402
from services.core.app import validation_inputs as vi  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

TENANT = UUID("11111111-1111-1111-1111-111111111111")
USER = UUID("44444444-4444-4444-4444-444444444444")
PROJECT = UUID("33333333-3333-3333-3333-333333333333")
URL = f"/internal/core/projects/{PROJECT}/validation-inputs"
GATEWAY = service_headers("gateway", **{"X-Tenant-Id": str(TENANT), "X-User-Id": str(USER)})
BODY = {
    "financial": {"discount_rate": 0.1, "cash_flows": [-100, 110], "horizon": "H1"},
    "growth": {"starting_mrr": 100, "new": 10},
}


class Tx:
    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store

    async def get_inputs(self, project_id: UUID, tenant_id: UUID) -> dict[str, Any] | None:
        if project_id != PROJECT or tenant_id != TENANT:
            return None
        return {"id": project_id, "inputs": self.store.get("inputs", {})}

    async def set_inputs(self, project_id: UUID, tenant_id: UUID, inputs: dict[str, Any]) -> None:
        self.store["inputs"] = inputs

    async def add_audit(self, **kwargs: Any) -> None:
        self.store.setdefault("audit", []).append(kwargs)


class Uow:
    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store

    async def __aenter__(self) -> Tx:
        return Tx(self.store)

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.fixture
def store() -> dict[str, Any]:
    return {}


@pytest.fixture
def client(store: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("ENVIRONMENT", "local")

    class Repo:
        engine = None

        def validation_input_unit_of_work(self, tenant_id: UUID) -> Uow:
            return Uow(store)

    core.app.state.repository = Repo()
    return testclient.TestClient(core.app)


def _put(client: Any, body: Any, if_match: str | None = "0", headers: Any = None) -> Any:
    sent = dict(headers or GATEWAY)
    if if_match is not None:
        sent["If-Match"] = if_match
    return client.put(URL, json=body, headers=sent)


def test_first_write_stores_the_object_with_version_and_etag(
    client: Any, store: dict[str, Any]
) -> None:
    response = _put(client, BODY)
    assert response.status_code == 200 and response.headers["ETag"] == '"1"'
    assert response.json()["validation"]["financial"]["cash_flows"] == [-100, 110]
    assert store["inputs"]["version"] == 1 and "outcomes" not in store["inputs"]["financial"]
    audit = store["audit"][0]
    assert audit["action"] == "validation.inputs_updated" and audit["tenant_id"] == TENANT
    assert audit["payload"]["sections"] == ["financial", "growth"]
    assert audit["payload"]["updated_by"] == str(USER)


def test_stale_etag_answers_412_and_writes_nothing(client: Any, store: dict[str, Any]) -> None:
    assert _put(client, BODY).status_code == 200
    stale = _put(client, {"growth": {"new": 1}}, if_match='"0"')
    assert stale.status_code == 412
    assert stale.json()["detail"] == {"error": "stale_etag", "current_version": 1}
    assert store["inputs"]["growth"]["new"] == 10 and len(store["audit"]) == 1


def test_second_write_bumps_version_and_replaces_sections(
    client: Any, store: dict[str, Any]
) -> None:
    _put(client, BODY)
    response = _put(client, {"growth": {"new": 1}}, if_match='W/"1"')
    assert response.headers["ETag"] == '"2"'
    assert "financial" not in store["inputs"] and store["inputs"]["growth"] == {"new": 1.0}


def test_same_content_keeps_the_version_and_adds_no_audit_row(
    client: Any, store: dict[str, Any]
) -> None:
    _put(client, BODY)
    response = _put(client, BODY, if_match='"1"')
    assert response.status_code == 200 and response.headers["ETag"] == '"1"'
    assert len(store["audit"]) == 1


def test_existing_funnel_section_is_carried_over(client: Any, store: dict[str, Any]) -> None:
    store["inputs"] = {"version": 3, "funnel": {"quota": 5}, "growth": {"new": 1}}
    _put(client, BODY, if_match='"3"')
    assert store["inputs"]["funnel"] == {"quota": 5} and store["inputs"]["version"] == 4


def test_missing_or_bad_if_match(client: Any) -> None:
    assert _put(client, BODY, if_match=None).status_code == 428
    assert _put(client, BODY, if_match="abc").status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {"unknown": {}},
        {"financial": {"discount_rate": 0.1, "cash_flows": [1], "extra": 1}},
        {"financial": {"discount_rate": 2, "cash_flows": [1]}},
        {"financial": {"discount_rate": 0.1, "cash_flows": []}},
        {"financial": {"discount_rate": 0.1, "cash_flows": [1], "horizon": "H9"}},
        {"unit_economics": {"monthly_churn": 1.5}},
        {"unit_economics": {"cogs": -1}},
        {"growth": {"new": "NaN"}},
        {"portfolio": {"revenue_by_horizon": {"H4": 1}}},
    ],
)
def test_invalid_objects_are_rejected_and_nothing_is_written(
    client: Any, store: dict[str, Any], body: dict[str, Any]
) -> None:
    assert _put(client, body).status_code == 422
    assert store == {}


def test_other_tenant_project_answers_404_and_writes_nothing(
    client: Any, store: dict[str, Any]
) -> None:
    other = service_headers("gateway", **{"X-Tenant-Id": "22222222-2222-2222-2222-222222222222"})
    assert _put(client, BODY, headers=other).status_code == 404
    assert store == {}


def test_only_the_gateway_is_allowed(client: Any, store: dict[str, Any]) -> None:
    for service in ("ml", "brief", "ingest", "seed"):
        headers = service_headers(service, **{"X-Tenant-Id": str(TENANT)})
        assert _put(client, BODY, headers=headers).status_code == 401
    assert _put(client, BODY, headers={"X-Tenant-Id": str(TENANT)}).status_code == 401
    assert store == {}


def test_no_database_answers_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("ENVIRONMENT", "local")
    core.app.state.repository = repo.CoreRepository(None)
    assert _put(testclient.TestClient(core.app), BODY).status_code == 503


async def test_sql_sets_tenant_first_then_locks_writes_only_the_validation_key_and_audits() -> None:
    engine = FakeEngine(
        [FakeResult(), FakeResult([{"id": PROJECT, "inputs": "{}"}]), FakeResult(), FakeResult()]
    )
    uow = repo.SqlValidationInputUnitOfWork(engine, TENANT)  # type: ignore[arg-type]
    inputs = vi.ValidationInputs.model_validate(BODY)
    body = await vi.update_inputs(uow, TENANT, PROJECT, inputs, if_match=0, updated_by=USER)
    assert body["version"] == 1
    calls = engine.connection.calls
    assert "set_config('app.tenant_id'" in calls[0][0]
    assert "FROM core.project" in calls[1][0] and "FOR UPDATE" in calls[1][0]
    assert "tenant_id = :tenant_id" in calls[1][0]
    assert "jsonb_set(metadata, '{validation}'" in calls[2][0]
    assert "tenant_id = :tenant_id" in calls[2][0]
    assert json.loads(calls[2][1]["inputs"])["version"] == 1
    assert "INSERT INTO core.audit_log" in calls[3][0] and calls[3][1]["tenant_id"] == TENANT


async def test_sql_missing_project_writes_nothing() -> None:
    engine = FakeEngine([FakeResult(), FakeResult()])
    uow = repo.SqlValidationInputUnitOfWork(engine, TENANT)  # type: ignore[arg-type]
    with pytest.raises(vi.ProjectNotFoundError):
        await vi.update_inputs(uow, TENANT, PROJECT, vi.ValidationInputs(), if_match=0)
    assert len(engine.connection.calls) == 2


def test_worker_reads_the_stored_object() -> None:
    from services.core.app import validation_worker as worker

    stored = vi.ValidationInputs.model_validate(BODY).model_dump(mode="json", exclude_none=True)
    out = worker.compute_project({"inputs": stored | {"version": 1}}, {}, [])
    assert out["financial_run"]["npv"] is not None and out["growth_metric"]["mrr"] == 110
