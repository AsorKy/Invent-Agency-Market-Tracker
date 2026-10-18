"""Unit tests for lead outcomes (REQ-COR-003, REQ-QA-002, principle P7).

A fake unit of work stages writes and restores state on rollback, proving that each outcome row
and its ``lead.outcome_recorded`` event commit together. The ``FakeStore`` and ``FakeActionUow``
defined here are reused by ``test_lead_actions.py``. No database is needed.
"""

import asyncio
from copy import deepcopy
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic")
from services.core.app.actions import (  # noqa: E402
    OutcomeCreate,
    outcome_label,
    record_outcome,
)
from services.core.app.leads import LeadNotFoundError  # noqa: E402

TENANT = uuid4()
LEAD_ID = uuid4()


class FakeStore:
    def __init__(self) -> None:
        self.leads: dict[UUID, dict[str, Any]] = {
            LEAD_ID: {
                "id": LEAD_ID,
                "tenant_id": TENANT,
                "company_id": uuid4(),
                "service_line": "saas",
                "stage": "hot",
                "version": 1,
            }
        }
        self.outcomes: list[dict[str, Any]] = []
        self.activities: list[dict[str, Any]] = []
        self.projects: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []
        self.log: list[str] = []
        self.fail_on: str | None = None

    def unit_of_work(self) -> "FakeActionUow":
        return FakeActionUow(self)


class FakeActionUow:
    def __init__(self, store: FakeStore) -> None:
        self.s = store

    async def __aenter__(self) -> "FakeActionUow":
        self.s.log.append("begin")
        self.snap = deepcopy((self.s.outcomes, self.s.activities, self.s.projects, self.s.outbox))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.s.log.append("commit")
        else:
            self.s.outcomes, self.s.activities, self.s.projects, self.s.outbox = self.snap
            self.s.log.append("rollback")

    def _step(self, name: str) -> None:
        self.s.log.append(name)
        if self.s.fail_on == name:
            raise RuntimeError(f"boom in {name}")

    async def get_lead(self, lead_id: UUID, tenant_id: UUID | None) -> dict[str, Any] | None:
        row = self.s.leads.get(lead_id)
        if row is None or (tenant_id and row["tenant_id"] != tenant_id):
            return None
        return dict(row)

    async def insert_outcome(self, row: dict[str, Any]) -> None:
        self._step("outcome")
        self.s.outcomes.append(dict(row))

    async def find_activity(self, tenant_id: UUID, key: str) -> dict[str, Any] | None:
        for row in self.s.activities:
            if row["tenant_id"] == tenant_id and row["idempotency_key"] == key:
                return row
        return None

    async def insert_activity(self, row: dict[str, Any]) -> None:
        self._step("activity")
        self.s.activities.append(dict(row))

    async def find_project_for_lead(self, lead_id: UUID) -> UUID | None:
        for row in self.s.projects:
            if row["lead_id"] == lead_id:
                return row["id"]  # type: ignore[no-any-return]
        return None

    async def insert_project(self, row: dict[str, Any]) -> None:
        self._step("project")
        self.s.projects.append(dict(row))

    async def add_outbox_event(self, **event: Any) -> None:
        self._step("outbox")
        self.s.outbox.append(event)


def _record(store: FakeStore, **body: Any) -> dict[str, Any]:
    return asyncio.run(
        record_outcome(store.unit_of_work(), LEAD_ID, OutcomeCreate(**body), request_id="r-1")
    )


@pytest.mark.parametrize(
    ("body", "label"),
    [
        ({"outcome": "contact"}, "contact"),
        ({"outcome": "response"}, "response"),
        ({"outcome": "meeting"}, "meeting"),
        ({"outcome": "spam"}, "spam"),
        ({"outcome": "close", "result": "won"}, "close_won"),
        ({"outcome": "close", "result": "lost"}, "close_lost"),
    ],
)
def test_every_outcome_is_stored_and_emitted(body: dict[str, Any], label: str) -> None:
    store = FakeStore()
    result = _record(store, **body)
    assert result["outcome"] == label
    assert outcome_label(OutcomeCreate(**body)) == label
    assert store.log == ["begin", "outcome", "outbox", "commit"]
    (row,) = store.outcomes
    assert row["tenant_id"] == TENANT and row["outcome"] == label
    (event,) = store.outbox
    assert event["event_type"] == "lead.outcome_recorded"
    assert event["payload"]["outcome"] == label
    assert event["payload"]["request_id"] == "r-1"
    assert event["aggregate_id"] == LEAD_ID


def test_close_requires_result_and_result_only_on_close() -> None:
    with pytest.raises(ValueError):
        OutcomeCreate(outcome="close")
    with pytest.raises(ValueError):
        OutcomeCreate(outcome="contact", result="won")
    with pytest.raises(ValueError):
        OutcomeCreate(outcome="nope")


def test_failure_after_outcome_rolls_back_both_rows() -> None:
    store = FakeStore()
    store.fail_on = "outbox"
    with pytest.raises(RuntimeError):
        _record(store, outcome="contact")
    assert store.outcomes == [] and store.outbox == []
    assert store.log[-1] == "rollback"


def test_unknown_lead_or_other_tenant() -> None:
    store = FakeStore()
    with pytest.raises(LeadNotFoundError):
        asyncio.run(record_outcome(store.unit_of_work(), uuid4(), OutcomeCreate(outcome="spam")))
    with pytest.raises(LeadNotFoundError):
        asyncio.run(
            record_outcome(
                store.unit_of_work(), LEAD_ID, OutcomeCreate(outcome="spam"), tenant_id=uuid4()
            )
        )
    assert store.outcomes == []
