"""Funnel HTTP routes: checklist state, gate decision, KILL sets status killed (T-146).

An in-memory store replaces the database: it copies its rows on entry and keeps them only on a
clean exit, so the rollback of a failed decision is observable. The SQL itself is covered by
``test_sql_consistency.py`` and, for the tenant context order, by a fake engine below.
"""

import copy
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")
testclient = pytest.importorskip("fastapi.testclient")
from support_cov_services import FakeEngine  # noqa: E402

from services.core.app import funnel as funnel_module  # noqa: E402
from services.core.app import main as core  # noqa: E402
from services.core.app.funnel import KILLED_STATUS, SqlFunnelStatusUnitOfWork  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

TENANT = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")
PROJECT = UUID("33333333-3333-3333-3333-333333333333")
LOCAL = {"APP_PROFILE": "local", "ENVIRONMENT": "local"}
GATEWAY = service_headers("gateway", **{"X-Tenant-Id": str(TENANT)})
BASE = f"/internal/core/funnel/projects/{PROJECT}"


class Store:
    def __init__(self) -> None:
        self.projects: dict[UUID, dict[str, Any]] = {
            PROJECT: {
                "id": PROJECT,
                "tenant_id": TENANT,
                "name": "Pilot",
                "status": "draft",
                "version": 1,
            }
        }
        self.audits: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.tenants_read: list[UUID | None] = []

    def unit_of_work(self, tenant: UUID) -> "Uow":
        return Uow(self, tenant)


class Uow:
    def __init__(self, store: Store, tenant: UUID) -> None:
        self.store, self.tenant = store, tenant

    async def __aenter__(self) -> "Tx":
        self.snapshot = copy.deepcopy((self.store.projects, self.store.audits, self.store.events))
        self.store.tenants_read.append(self.tenant)
        return Tx(self.store, self.tenant)

    async def __aexit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type is not None:
            self.store.projects, self.store.audits, self.store.events = self.snapshot


class Tx:
    def __init__(self, store: Store, tenant: UUID) -> None:
        self.store, self.tenant = store, tenant

    def _own(self, project_id: UUID) -> dict[str, Any] | None:
        row = self.store.projects.get(project_id)
        return row if row and row["tenant_id"] == self.tenant else None

    async def get_project(self, project_id: UUID, tenant_id: UUID | None) -> dict[str, Any] | None:
        row = self._own(project_id)
        return dict(row) if row else None

    read_project = get_project

    async def last_decisions(self, project_id: UUID, tenant_id: UUID) -> list[dict[str, Any]]:
        return [
            {"payload": a["payload"], "created_at": None}
            for a in reversed(self.store.audits)
            if a["project_id"] == project_id and a["tenant_id"] == tenant_id
        ]

    async def add_audit(self, **kw: Any) -> None:
        self.store.audits.append(kw)

    async def add_outbox_event(self, **kw: Any) -> None:
        self.store.events.append(kw)

    async def mark_killed(
        self, project_id: UUID, tenant_id: UUID | None, *, expected_version: int
    ) -> bool:
        row = self._own(project_id)
        if not row or row["version"] != expected_version or row["status"] == KILLED_STATUS:
            return False
        row["status"], row["version"] = KILLED_STATUS, expected_version + 1
        return True


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    data = Store()
    monkeypatch.setattr(
        core, "funnel_unit_of_work", lambda _engine, tenant: data.unit_of_work(tenant)
    )
    for key, value in LOCAL.items():
        monkeypatch.setenv(key, value)
    return data


@pytest.fixture
def client(store: Store) -> Any:
    class Repo:
        engine = None

    core.app.state.repository = Repo()
    return testclient.TestClient(core.app)


def decide(
    client: Any, inputs: dict[str, Any], version: int = 1, tenant: UUID = TENANT, gate: str = "G3"
) -> Any:
    headers = service_headers("gateway", **{"X-Tenant-Id": str(tenant)})
    return client.post(
        f"{BASE}/gates/{gate}/decision",
        json={"inputs": inputs, "expected_version": version},
        headers=headers,
    )


def kill_inputs() -> dict[str, Any]:
    from libs.domain.funnel import load_checklist

    item = next(i for i in load_checklist("G3").items if i.type == "kill")
    bad = {
        "gt": item.threshold - 1,
        "gte": item.threshold - 1,
        "lt": item.threshold + 1,
        "lte": item.threshold + 1,
        "eq": item.threshold + 1,
        "ne": item.threshold,
    }[item.op]
    return {item.id: bad}


def test_state_lists_gates_and_status(client: Any) -> None:
    body = client.get(BASE, headers=GATEWAY).json()
    assert body["status"] == "draft" and body["version"] == 1 and body["killed"] is False
    assert [g["gate"] for g in body["gates"]] == [f"G{i}" for i in range(1, 8)]
    assert body["gates"][2]["available"] and body["gates"][2]["items"]
    assert body["gates"][2]["last_decision"] is None


def test_state_shows_last_decision(client: Any) -> None:
    assert decide(client, {}).json()["verdict"] == "WAIT"
    last = client.get(BASE, headers=GATEWAY).json()["gates"][2]["last_decision"]
    assert last["verdict"] == "WAIT"


def test_other_tenant_gets_404_on_both_routes(client: Any) -> None:
    headers = service_headers("gateway", **{"X-Tenant-Id": str(OTHER)})
    assert client.get(BASE, headers=headers).status_code == 404
    assert decide(client, {}, tenant=OTHER).status_code == 404


def test_tenant_header_is_required(client: Any) -> None:
    headers = service_headers("gateway")
    assert client.get(BASE, headers=headers).status_code == 400
    response = client.post(
        f"{BASE}/gates/G3/decision", json={"inputs": {}, "expected_version": 1}, headers=headers
    )
    assert response.status_code == 400


def test_only_the_gateway_is_allowed(client: Any) -> None:
    for service in ("ml", "brief", "ingest", "seed"):
        headers = service_headers(service, **{"X-Tenant-Id": str(TENANT)})
        assert client.get(BASE, headers=headers).status_code == 401
        assert client.post(f"{BASE}/gates/G3/decision", json={}, headers=headers).status_code == 401
    assert client.get(BASE, headers={"X-Tenant-Id": str(TENANT)}).status_code == 401


def test_unknown_gate_and_bad_body(client: Any) -> None:
    assert decide(client, {}, gate="G9").status_code == 404
    assert (
        client.post(f"{BASE}/gates/G3/decision", json={"inputs": {}}, headers=GATEWAY).status_code
        == 422
    )


def test_non_kill_decision_keeps_status_and_audits(client: Any, store: Store) -> None:
    assert decide(client, {}).status_code == 200
    assert store.projects[PROJECT]["status"] == "draft"
    assert len(store.audits) == 1 and store.audits[0]["action"] == "funnel.gate_decision"


def test_kill_sets_status_and_audit_in_one_transaction(client: Any, store: Store) -> None:
    response = decide(client, kill_inputs())
    assert response.status_code == 200 and response.json()["verdict"] == "KILL"
    assert store.projects[PROJECT]["status"] == "killed" == KILLED_STATUS
    assert store.projects[PROJECT]["version"] == 2
    assert [a["payload"]["verdict"] for a in store.audits] == ["KILL"]
    assert "funnel.project_killed" in [e["event_type"] for e in store.events]
    assert client.get(BASE, headers=GATEWAY).json()["killed"] is True


def test_decision_on_killed_project_is_refused(client: Any, store: Store) -> None:
    assert decide(client, kill_inputs()).status_code == 200
    audits = len(store.audits)
    again = decide(client, {}, version=2)
    assert again.status_code == 409 and again.json()["detail"] == "project_killed"
    assert len(store.audits) == audits


def test_stale_version_is_refused_without_writes(client: Any, store: Store) -> None:
    response = decide(client, kill_inputs(), version=5)
    assert response.status_code == 409 and response.json()["detail"] == "stale_project_version"
    assert store.audits == [] and store.projects[PROJECT]["status"] == "draft"


def test_lost_kill_race_rolls_back_the_audit_row(
    client: Any, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def lost(self: Tx, *_a: Any, **_k: Any) -> bool:
        return False

    monkeypatch.setattr(Tx, "mark_killed", lost)
    assert decide(client, kill_inputs()).status_code == 409
    assert store.audits == [] and store.events == []


def test_production_refuses_example_checklists(
    client: Any, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("FUNNEL_ALLOW_EXAMPLE_CHECKLISTS", raising=False)
    refused = decide(client, {})
    assert refused.status_code == 422 and refused.json()["detail"] == "example_checklist_refused"
    assert store.audits == []
    state = client.get(BASE, headers=GATEWAY).json()
    assert state["gates"][2]["available"] is False
    monkeypatch.setenv("FUNNEL_ALLOW_EXAMPLE_CHECKLISTS", "true")
    assert decide(client, {}).status_code == 200


async def test_sql_unit_of_work_sets_tenant_before_any_statement() -> None:
    engine = FakeEngine()
    uow = funnel_module.funnel_unit_of_work(engine, TENANT)  # type: ignore[arg-type]
    assert isinstance(uow, SqlFunnelStatusUnitOfWork)
    async with uow as tx:
        await tx.get_project(uuid4(), TENANT)
    statements = [call[0] for call in engine.connection.calls]
    assert "set_config('app.tenant_id'" in statements[0]
    assert "FROM core.project" in statements[1]


def test_funnel_unit_of_work_needs_a_database() -> None:
    with pytest.raises(RuntimeError):
        funnel_module.funnel_unit_of_work(None, TENANT)
