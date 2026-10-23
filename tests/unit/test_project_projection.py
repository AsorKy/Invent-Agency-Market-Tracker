"""Unit tests for the project projection of the Pulse consumer (T-152, REQ-EVT-002)."""

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic_settings")
pytest.importorskip("structlog")
pytest.importorskip("redis.asyncio")

from services.pulse.app import consumer  # noqa: E402

TENANT = uuid4()


class _Result:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class FakeConnection:
    """Emulate the statements of the projection with the ON CONFLICT semantics."""

    def __init__(self) -> None:
        self.processed: set[UUID] = set()
        self.snapshots: dict[tuple[UUID, UUID], dict[str, Any]] = {}
        self.visits: list[dict[str, Any]] = []
        self.contexts: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        params = params or {}
        if "set_config('app.tenant_id'" in sql:
            self.contexts.append(params["tenant_id"])
            return _Result()
        if "INSERT INTO pulse.processed_event" in sql:
            if params["event_id"] in self.processed:
                return _Result()
            self.processed.add(params["event_id"])
            return _Result(params["event_id"])
        if "INSERT INTO pulse.project_snapshot" in sql:
            key = (params["tenant_id"], params["project_id"])
            if key not in self.snapshots:
                self.snapshots[key] = params
                self.visits.append(dict(params))
            return _Result()
        if "UPDATE pulse.project_snapshot" in sql:
            row = self.snapshots.get((params["tenant_id"], params["project_id"]))
            if row is None or row["stage"] == params["stage"]:
                return _Result()
            row["stage"] = params["stage"]
            return _Result(params["project_id"])
        if "UPDATE pulse.project_stage_visit" in sql:
            for visit in self.visits:
                if visit["project_id"] == params["project_id"] and not visit.get("exited"):
                    visit["exited"] = True
            return _Result()
        if "INSERT INTO pulse.project_stage_visit" in sql:
            self.visits.append(dict(params))
            return _Result()
        raise AssertionError(f"unexpected SQL: {sql}")


def message(project_id: UUID, tenant: UUID | None = TENANT) -> dict[str, str]:
    fields = {
        "event_id": str(uuid4()),
        "event_type": "signal.converted_to_project",
        "aggregate_type": "project",
        "aggregate_id": str(project_id),
        "version": "1",
        "payload": json.dumps(
            {"project_id": str(project_id), "lead_id": str(uuid4()), "service_line": "web"}
        ),
    }
    if tenant is not None:
        fields["tenant_id"] = str(tenant)
    return fields


async def test_conversion_writes_snapshot_and_first_visit() -> None:
    db, project = FakeConnection(), uuid4()
    await consumer.process_event(db, message(project))  # type: ignore[arg-type]
    row = db.snapshots[(TENANT, project)]
    assert (row["stage"], row["service_line"]) == ("draft", "web")
    assert len(db.visits) == 1
    assert db.contexts == [str(TENANT)]


async def test_same_event_twice_changes_nothing() -> None:
    db, project = FakeConnection(), uuid4()
    event = message(project)
    await consumer.process_event(db, event)  # type: ignore[arg-type]
    await consumer.process_event(db, event)  # type: ignore[arg-type]
    assert len(db.snapshots) == 1 and len(db.visits) == 1


async def test_new_event_for_same_project_adds_no_second_visit() -> None:
    db, project = FakeConnection(), uuid4()
    await consumer.process_event(db, message(project))  # type: ignore[arg-type]
    await consumer.process_event(db, message(project))  # type: ignore[arg-type]
    assert len(db.visits) == 1


async def test_event_without_tenant_writes_nothing() -> None:
    db = FakeConnection()
    await consumer.process_event(db, message(uuid4(), None))  # type: ignore[arg-type]
    assert db.snapshots == {} and db.contexts == []


def stage_message(event_type: str, project_id: UUID | None, **payload: Any) -> dict[str, str]:
    body = {"lead_id": str(uuid4()), "project_id": str(project_id) if project_id else None}
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "tenant_id": str(TENANT),
        "payload": json.dumps({**body, **payload, "parent_event_id": str(uuid4())}),
    }


async def converted(db: FakeConnection, project: UUID) -> None:
    await consumer.process_event(db, message(project))  # type: ignore[arg-type]


def open_stages(db: FakeConnection) -> list[str]:
    return [v["stage"] for v in db.visits if not v.get("exited")]


async def test_pipeline_updated_moves_stage_and_closes_visit() -> None:
    db, project = FakeConnection(), uuid4()
    await converted(db, project)
    event = stage_message(
        "project.pipeline_updated", project, **{"from": "draft", "to": "hot", "version": 2}
    )
    await consumer.process_event(db, event)  # type: ignore[arg-type]
    assert db.snapshots[(TENANT, project)]["stage"] == "hot"
    assert db.visits[0]["exited"] is True
    assert open_stages(db) == ["hot"]


async def test_forecast_updated_alone_moves_stage_without_figures() -> None:
    db, project = FakeConnection(), uuid4()
    await converted(db, project)
    event = stage_message("project.forecast_updated", project, stage="won")
    await consumer.process_event(db, event)  # type: ignore[arg-type]
    row = db.snapshots[(TENANT, project)]
    assert row["stage"] == "won" and open_stages(db) == ["won"]
    assert not {"score", "pipeline_value"} & set(row)


async def test_pipeline_then_forecast_adds_one_visit_and_replay_changes_nothing() -> None:
    db, project = FakeConnection(), uuid4()
    await converted(db, project)
    first = stage_message("project.pipeline_updated", project, **{"to": "hot"})
    second = stage_message("project.forecast_updated", project, stage="hot")
    for event in (first, second, first, second):
        await consumer.process_event(db, event)  # type: ignore[arg-type]
    assert len(db.visits) == 2 and open_stages(db) == ["hot"]


async def test_stage_event_without_project_or_snapshot_changes_nothing() -> None:
    db = FakeConnection()
    await consumer.process_event(db, stage_message("project.forecast_updated", None, stage="hot"))  # type: ignore[arg-type]
    await consumer.process_event(
        db,  # type: ignore[arg-type]
        stage_message("project.forecast_updated", uuid4(), stage="hot"),
    )
    assert db.snapshots == {} and db.visits == []
