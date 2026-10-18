"""Unit tests for automatic qualification (REQ-COR-008, REQ-QA-002).

A fake unit of work stages writes and restores state on rollback. Proves lead creation with
``lead.created``, forward-only advancement by the tenant thresholds, respect for human-set
status, inbox idempotency, and the worker handler wiring. No database or Redis is needed.
"""

import asyncio
from copy import deepcopy
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic")
from libs.domain.pipeline import PipelineStage as S  # noqa: E402
from services.core.app.qualification import (  # noqa: E402
    advance_path,
    handle_score_event,
    parse_score_event,
    target_stage,
)
from services.core.app.tenant_config import TenantConfig, config_to_row  # noqa: E402

TENANT = uuid4()
COMPANY = uuid4()


class Store:
    def __init__(self) -> None:
        self.companies: dict[UUID, UUID | None] = {COMPANY: TENANT}
        self.configs: dict[UUID, dict[str, Any]] = {}
        self.leads: dict[UUID, dict[str, Any]] = {}
        self.processed: set[UUID] = set()
        self.audit: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []
        self.fail_on: str | None = None


class FakeUow:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def __aenter__(self) -> "FakeUow":
        self.snap = deepcopy((self.s.leads, self.s.processed, self.s.audit, self.s.outbox))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.s.leads, self.s.processed, self.s.audit, self.s.outbox = self.snap

    async def mark_processed(self, event_id: UUID) -> bool:
        if event_id in self.s.processed:
            return False
        self.s.processed.add(event_id)
        return True

    async def lock_key(self, key: str) -> None:
        pass

    async def get_company_tenant(self, company_id: UUID) -> tuple[bool, UUID | None]:
        return company_id in self.s.companies, self.s.companies.get(company_id)

    async def get_config(self, tenant_id: UUID) -> dict[str, Any] | None:
        return self.s.configs.get(tenant_id)

    async def find_leads(self, company_id: UUID) -> list[dict[str, Any]]:
        return [dict(r) for r in self.s.leads.values() if r["company_id"] == company_id]

    async def has_manual_transition(self, lead_id: UUID) -> bool:
        return any(
            a["lead_id"] == lead_id and a["action"] == "lead.status_changed" for a in self.s.audit
        )

    async def insert_lead(self, row: dict[str, Any]) -> None:
        self.s.leads[row["id"]] = dict(row)

    async def update_stage(self, row: dict[str, Any]) -> None:
        if self.s.fail_on == "update":
            raise RuntimeError("boom")
        self.s.leads[row["id"]].update(row)

    async def add_audit(self, **kw: Any) -> None:
        self.s.audit.append(kw)

    async def add_outbox_event(self, **kw: Any) -> None:
        self.s.outbox.append(kw)


def _event(score: int, company: UUID = COMPANY, event_id: UUID | None = None) -> dict[str, str]:
    return {
        "event_id": str(event_id or uuid4()),
        "event_type": "lead.score_updated",
        "aggregate_id": str(company),
        "score": str(score),
    }


def _run(store: Store, fields: dict[str, str]) -> str:
    return asyncio.run(handle_score_event(FakeUow(store), fields))


def _only_lead(store: Store) -> dict[str, Any]:
    (lead,) = store.leads.values()
    return lead


def test_pure_rules() -> None:
    assert target_stage(70, 70, 50) is S.HOT
    assert target_stage(69, 70, 50) is S.QUALIFIED
    assert target_stage(50, 70, 50) is S.QUALIFIED
    assert target_stage(49, 70, 50) is S.DISCOVERED
    assert advance_path(S.DISCOVERED, S.HOT) == [S.QUALIFIED, S.HOT]
    assert advance_path(S.QUALIFIED, S.HOT) == [S.HOT]
    assert advance_path(S.HOT, S.QUALIFIED) == []
    assert advance_path(S.CONTACTED, S.HOT) == []
    assert advance_path(S.DISCOVERED, S.CONTACTED) == []


def test_parse_rejects_bad_events() -> None:
    for bad in ({}, _event(101), _event(-1), _event(1) | {"score": "x"}):
        with pytest.raises(ValueError):
            parse_score_event(bad)
    assert parse_score_event(_event(5) | {"service_line": "saas"})[3] == "saas"


def test_low_score_creates_discovered_lead_with_event() -> None:
    store = Store()
    assert _run(store, _event(10)) == "created"
    lead = _only_lead(store)
    assert lead["stage"] == "discovered" and lead["tenant_id"] == TENANT
    (event,) = store.outbox
    assert event["payload"]["origin"] == "auto_qualification"
    assert store.audit == []


def test_high_score_creates_and_advances_to_hot_with_audit_per_step() -> None:
    store = Store()
    _run(store, _event(85))
    lead = _only_lead(store)
    assert lead["stage"] == "hot" and lead["version"] == 3
    assert [a["payload"]["to"] for a in store.audit] == ["qualified", "hot"]
    assert len(store.outbox) == 1


def test_scores_never_move_backward() -> None:
    store = Store()
    _run(store, _event(60))
    assert _only_lead(store)["stage"] == "qualified"
    assert _run(store, _event(5)) == "processed"
    assert _only_lead(store)["stage"] == "qualified"
    _run(store, _event(90))
    assert _only_lead(store)["stage"] == "hot"
    _run(store, _event(55))
    assert _only_lead(store)["stage"] == "hot"
    assert len(store.outbox) == 1


def test_human_set_and_later_stages_are_left_alone() -> None:
    store = Store()
    _run(store, _event(10))
    lead = _only_lead(store)
    store.audit.append({"action": "lead.status_changed", "lead_id": lead["id"], "payload": {}})
    _run(store, _event(95))
    assert _only_lead(store)["stage"] == "discovered"
    lead["stage"] = "contacted"
    store.leads[lead["id"]]["stage"] = "contacted"
    store.audit.clear()
    _run(store, _event(95))
    assert _only_lead(store)["stage"] == "contacted"


def test_event_redelivery_is_a_noop() -> None:
    store = Store()
    event = _event(85)
    assert _run(store, event) == "created"
    snapshot = deepcopy(store.leads)
    assert _run(store, event) == "duplicate"
    assert store.leads == snapshot and len(store.audit) == 2


def test_tenant_thresholds_override_defaults() -> None:
    store = Store()
    config = TenantConfig.model_validate(
        {"brief_rules": {"alert_rules": {"min_score_for_hot": 90, "min_score_for_qualified": 20}}}
    )
    store.configs[TENANT] = config_to_row(config) | {"version": 1}
    _run(store, _event(80))
    assert _only_lead(store)["stage"] == "qualified"
    _run(store, _event(90))
    assert _only_lead(store)["stage"] == "hot"


def test_unknown_company_is_acknowledged_without_lead() -> None:
    store = Store()
    assert _run(store, _event(80, company=uuid4())) == "unknown_company"
    assert store.leads == {}


def test_company_without_tenant_uses_defaults() -> None:
    store = Store()
    store.companies[COMPANY] = None
    _run(store, _event(75))
    assert _only_lead(store)["stage"] == "hot"


def test_failure_rolls_back_inbox_lead_and_events() -> None:
    store = Store()
    store.fail_on = "update"
    event = _event(85)
    with pytest.raises(RuntimeError):
        _run(store, event)
    assert store.leads == {} and store.processed == set() and store.outbox == []
    store.fail_on = None
    assert _run(store, event) == "created"


def test_worker_handler_dispatches_score_events_only() -> None:
    from libs.common.events_dispatch import dispatch_event
    from services.core.app.worker import build_handlers

    store = Store()
    handlers = build_handlers(lambda: FakeUow(store))
    acked: list[int] = []

    async def ack() -> None:
        acked.append(1)

    fields = _event(85)
    assert asyncio.run(dispatch_event(fields, handlers, ack)) is True
    assert asyncio.run(dispatch_event({"event_type": "signal.created"}, handlers, ack)) is False
    assert len(acked) == 2 and _only_lead(store)["stage"] == "hot"
