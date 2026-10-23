"""Unit tests for lead activities and conversion to a project (REQ-COR-005, REQ-QA-002).

Reuses the fake unit of work from ``test_outcomes.py``. Proves the activity row and
``signal.activity_logged`` event, idempotent activity replay, the single-transaction conversion
with ``signal.converted_to_project``, and the HTTP routes through a fake repository.
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic")
from test_outcomes import LEAD_ID, TENANT, FakeStore  # noqa: E402

from services.core.app.actions import (  # noqa: E402
    ActivityCreate,
    LeadAlreadyConvertedError,
    LeadClosedError,
    ProjectCreate,
    convert_to_project,
    log_activity,
)
from services.core.app.leads import LeadNotFoundError  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402


def _activity(store: FakeStore, key: str | None = None, **over: Any) -> tuple[dict[str, Any], bool]:
    body = ActivityCreate(**({"activity_type": "call", "channel": "phone"} | over))
    return asyncio.run(
        log_activity(
            store.unit_of_work(),
            LEAD_ID,
            body,
            tenant_id=TENANT,
            idempotency_key=key,
            request_id="r-2",
        )
    )


def _convert(store: FakeStore, **over: Any) -> dict[str, Any]:
    body = ProjectCreate(
        **({"lead_id": LEAD_ID, "name": "Pilot", "hypothesis": "They need X"} | over)
    )
    return asyncio.run(convert_to_project(store.unit_of_work(), body, request_id="r-3"))


def test_activity_is_written_with_event() -> None:
    store = FakeStore()
    body, created = _activity(store)
    assert created
    assert store.log == ["begin", "activity", "outbox", "commit"]
    (row,) = store.activities
    assert row["channel"] == "phone" and row["tenant_id"] == TENANT
    (event,) = store.outbox
    assert event["event_type"] == "signal.activity_logged"
    assert event["payload"]["activity_id"] == body["activity_id"]


def test_activity_idempotency_key_replays_without_second_write() -> None:
    store = FakeStore()
    first, _ = _activity(store, key="a1")
    again, created = _activity(store, key="a1")
    assert not created and again == first
    assert len(store.activities) == 1 and len(store.outbox) == 1


def test_activity_for_unknown_lead_and_bad_type() -> None:
    store = FakeStore()
    with pytest.raises(LeadNotFoundError):
        asyncio.run(
            log_activity(
                store.unit_of_work(),
                uuid4(),
                ActivityCreate(activity_type="note"),
                tenant_id=TENANT,
            )
        )
    with pytest.raises(ValueError):
        ActivityCreate(activity_type="fax")


def test_conversion_creates_project_and_event_in_one_transaction() -> None:
    store = FakeStore()
    result = _convert(store, target_market="Medellin", estimated_roi=2.5, timeline="Q1")
    assert store.log == ["begin", "project", "outbox", "commit"]
    (project,) = store.projects
    assert project["lead_id"] == LEAD_ID and project["service_line"] == "saas"
    assert project["metadata"] == {"target_market": "Medellin", "timeline": "Q1"}
    assert result["project_id"] == str(project["id"])
    (event,) = store.outbox
    assert event["event_type"] == "signal.converted_to_project"
    assert event["aggregate_type"] == "project"


def test_conversion_failure_rolls_back_project() -> None:
    store = FakeStore()
    store.fail_on = "outbox"
    with pytest.raises(RuntimeError):
        _convert(store)
    assert store.projects == [] and store.outbox == []


def test_conversion_rejects_duplicates_lost_and_unknown() -> None:
    store = FakeStore()
    _convert(store)
    with pytest.raises(LeadAlreadyConvertedError) as dup:
        _convert(store)
    assert dup.value.project_id == store.projects[0]["id"]
    store.projects.clear()
    store.leads[LEAD_ID]["stage"] = "lost"
    with pytest.raises(LeadClosedError):
        _convert(store)
    with pytest.raises(LeadNotFoundError):
        _convert(store, lead_id=uuid4())
    assert store.projects == []


def test_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from services.core.app import main

    store = FakeStore()

    class Repo:
        def action_unit_of_work(self, tenant_id: Any = None) -> Any:
            return store.unit_of_work()

        async def list_projects(self, limit: int, cursor: str | None = None, **kw: Any) -> Any:
            if cursor == "bad":
                raise ValueError("invalid_cursor")
            self.kw = kw
            return [{"id": "p"}], None

    repo = Repo()
    main.app.state.repository = repo
    client = TestClient(main.app, headers=service_headers())
    try:
        base = f"/internal/core/leads/{LEAD_ID}"
        tenant = {"X-Tenant-ID": str(TENANT)}
        out = client.post(f"{base}/outcomes", json={"outcome": "close", "result": "won"})
        assert out.status_code == 201 and out.json()["outcome"] == "close_won"
        assert client.post(f"{base}/outcomes", json={"outcome": "close"}).status_code == 422
        missing = client.post(f"/internal/core/leads/{uuid4()}/outcomes", json={"outcome": "spam"})
        assert missing.status_code == 404
        body = {"activity_type": "email"}
        assert client.post(f"{base}/activities", json=body).status_code == 422
        headers = tenant | {"Idempotency-Key": "k"}
        first = client.post(f"{base}/activities", json=body, headers=headers)
        second = client.post(f"{base}/activities", json=body, headers=headers)
        assert (first.status_code, second.status_code) == (201, 200)
        project = {"lead_id": str(LEAD_ID), "name": "P", "hypothesis": "H"}
        made = client.post("/internal/core/projects", json=project)
        assert made.status_code == 201
        again = client.post("/internal/core/projects", json=project)
        assert again.status_code == 409
        assert again.json()["detail"]["project_id"] == made.json()["project_id"]
        store.projects.clear()
        store.leads[LEAD_ID]["stage"] = "lost"
        assert client.post("/internal/core/projects", json=project).status_code == 422
        unknown = client.post("/internal/core/projects", json=project | {"lead_id": str(uuid4())})
        assert unknown.status_code == 404
        listing = client.get("/internal/core/projects?status=draft")
        assert listing.status_code == 200 and repo.kw["status"] == "draft"
        assert client.get("/internal/core/projects?cursor=bad").status_code == 400
        assert UUID(made.json()["lead_id"]) == LEAD_ID
    finally:
        main.app.dependency_overrides.clear()
