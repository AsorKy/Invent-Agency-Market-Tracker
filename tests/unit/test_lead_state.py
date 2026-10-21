"""Unit tests for the lead state machine and creation (REQ-COR-002, REQ-QA-002).

Pure transition rules run without I/O; ``create_lead`` and ``change_status`` run against a fake
unit of work that stages writes, so idempotent replay, 409 conflicts, and atomic commit are proven
without a database. The HTTP routes are checked with a fake repository.
"""

import asyncio
from copy import deepcopy
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic")
from libs.domain.pipeline import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    PipelineStage,
    VersionConflictError,
    allowed_targets,
    can_transition,
    check_version,
    group_by_stage,
    parse_stage,
    validate_transition,
)
from services.core.app.leads import (  # noqa: E402
    IdempotencyKeyReuseError,
    LeadConflictError,
    LeadCreate,
    LeadNotFoundError,
    LeadStatusPatch,
    LeadTransitionError,
    change_status,
    create_lead,
)
from tests.unit.service_auth import service_headers  # noqa: E402

S = PipelineStage
TENANT = uuid4()


def test_happy_path_and_lost_from_hot_onward() -> None:
    chain = [S.DISCOVERED, S.QUALIFIED, S.HOT, S.CONTACTED, S.MEETING, S.PROPOSAL, S.WON]
    for a, b in zip(chain, chain[1:], strict=False):
        assert can_transition(a, b)
    for stage in (S.HOT, S.CONTACTED, S.MEETING, S.PROPOSAL, S.NEW):
        assert can_transition(stage, S.LOST)
    for stage in (S.DISCOVERED, S.QUALIFIED):
        assert not can_transition(stage, S.LOST)


def test_no_backward_skip_or_terminal_exit() -> None:
    assert not can_transition(S.HOT, S.QUALIFIED)
    assert not can_transition(S.QUALIFIED, S.CONTACTED)
    for terminal in (S.WON, S.LOST):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
        assert allowed_targets(terminal) == []
    assert allowed_targets(S.HOT) == [S.CONTACTED, S.LOST]


def test_parse_stage_and_validation_order() -> None:
    assert parse_stage("hot") is S.HOT
    with pytest.raises(ValueError, match="unknown_stage"):
        parse_stage("bogus")
    check_version(2, 2)
    with pytest.raises(VersionConflictError) as conflict:
        validate_transition(S.WON, S.HOT, expected_version=1, version=2)
    assert (conflict.value.expected, conflict.value.current) == (1, 2)
    with pytest.raises(InvalidTransitionError) as invalid:
        validate_transition(S.HOT, S.WON, expected_version=2, version=2)
    assert invalid.value.allowed == [S.CONTACTED, S.LOST]
    validate_transition(S.HOT, S.CONTACTED, expected_version=2, version=2)


def test_group_by_stage_keeps_empty_columns() -> None:
    columns = group_by_stage([{"stage": "hot", "id": 1}, {"stage": "hot", "id": 2}])
    assert [lead["id"] for lead in columns["hot"]] == [1, 2]
    assert columns["won"] == []
    assert list(columns)[0] == "discovered"
    assert group_by_stage([{"stage": "legacy"}])["legacy"] == [{"stage": "legacy"}]


class Store:
    def __init__(self) -> None:
        self.leads: dict[UUID, dict[str, Any]] = {}
        self.idem: dict[tuple[UUID, str], dict[str, Any]] = {}
        self.outbox: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.log: list[str] = []


class FakeUow:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def __aenter__(self) -> "FakeUow":
        self.s.log.append("begin")
        self.snap = deepcopy((self.s.leads, self.s.idem, self.s.outbox, self.s.audit))
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
            self.s.leads, self.s.idem, self.s.outbox, self.s.audit = self.snap
            self.s.log.append("rollback")

    async def lock_key(self, key: str) -> None:
        pass

    async def get_idempotency(self, tenant_id: UUID, key: str) -> dict[str, Any] | None:
        return self.s.idem.get((tenant_id, key))

    async def save_idempotency(self, *, tenant_id: UUID, key: str, **kw: Any) -> None:
        self.s.idem[(tenant_id, key)] = {
            "request_hash": kw["request_hash"],
            "response_body": kw["response"],
        }

    async def insert_lead(self, row: dict[str, Any]) -> None:
        self.s.leads[row["id"]] = dict(row)

    async def get_lead(self, lead_id: UUID, tenant_id: UUID | None) -> dict[str, Any] | None:
        row = self.s.leads.get(lead_id)
        if row is None or (tenant_id and row["tenant_id"] != tenant_id):
            return None
        return dict(row)

    async def update_stage(self, row: dict[str, Any]) -> None:
        self.s.leads[row["id"]] = dict(row)

    async def add_audit(self, **kw: Any) -> None:
        self.s.audit.append(kw)

    async def add_outbox_event(self, **kw: Any) -> None:
        self.s.outbox.append(kw)


def _create(store: Store, key: str = "k1", **over: Any) -> tuple[dict[str, Any], bool]:
    body = LeadCreate(company_id=UUID(int=1), service_line="saas", **over)
    return asyncio.run(
        create_lead(FakeUow(store), body, tenant_id=TENANT, idempotency_key=key, request_id="r")
    )


def test_create_is_idempotent() -> None:
    store = Store()
    first, created = _create(store)
    again, created_again = _create(store)
    assert created and not created_again
    assert again == first
    assert len(store.leads) == 1
    assert len(store.outbox) == 1
    assert store.outbox[0]["payload"]["origin"] == "manual"
    assert first["stage"] == "discovered"
    assert first["version"] == 1


def test_key_reuse_with_other_body_is_rejected() -> None:
    store = Store()
    _create(store)
    with pytest.raises(IdempotencyKeyReuseError):
        _create(store, city="Cali")
    assert len(store.leads) == 1
    assert store.log[-1] == "rollback"


def _patch(store: Store, lead_id: UUID, stage: S, version: int) -> dict[str, Any]:
    return asyncio.run(
        change_status(
            FakeUow(store), lead_id, LeadStatusPatch(stage=stage, expected_version=version)
        )
    )


def test_status_change_bumps_version_and_audits() -> None:
    store = Store()
    created, _ = _create(store)
    lead_id = UUID(created["lead_id"])
    store.leads[lead_id]["stage"] = "hot"
    result = _patch(store, lead_id, S.CONTACTED, 1)
    assert result == {"lead_id": str(lead_id), "stage": "contacted", "version": 2}
    assert store.leads[lead_id]["last_transition_at"] is not None
    assert store.audit[-1]["payload"]["from"] == "hot"


def test_stale_version_conflicts_with_current_state() -> None:
    store = Store()
    created, _ = _create(store)
    lead_id = UUID(created["lead_id"])
    store.leads[lead_id].update(stage="qualified", version=3)
    with pytest.raises(LeadConflictError) as conflict:
        _patch(store, lead_id, S.HOT, 1)
    assert conflict.value.current["stage"] == "qualified"
    assert conflict.value.current["version"] == 3
    assert store.leads[lead_id]["stage"] == "qualified"


def test_illegal_transition_and_missing_lead() -> None:
    store = Store()
    created, _ = _create(store)
    lead_id = UUID(created["lead_id"])
    with pytest.raises(LeadTransitionError) as bad:
        _patch(store, lead_id, S.WON, 1)
    assert bad.value.allowed == ["qualified"]
    with pytest.raises(LeadNotFoundError):
        _patch(store, uuid4(), S.QUALIFIED, 1)


def test_patch_requires_expected_version() -> None:
    with pytest.raises(ValueError):
        LeadStatusPatch.model_validate({"stage": "hot"})


def test_routes_use_repository_and_return_409(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from services.core.app import main

    store = Store()

    class Repo:
        def lead_unit_of_work(self) -> FakeUow:
            return FakeUow(store)

        async def list_leads(self, limit: int, cursor: str | None = None, **kw: Any) -> Any:
            self.kw = kw
            return [], None

        async def get_lead(self, lead_id: UUID, tenant_id: UUID | None = None) -> Any:
            return {"id": lead_id, "version": 4} if lead_id.int else None

    repo = Repo()
    main.app.state.repository = repo
    client = TestClient(main.app, headers=service_headers())
    try:
        h = {"Idempotency-Key": "abc", "X-Tenant-ID": str(TENANT)}
        body = {"company_id": str(UUID(int=1)), "service_line": "saas"}
        assert client.post("/internal/core/leads", json=body).status_code == 422
        first = client.post("/internal/core/leads", json=body, headers=h)
        second = client.post("/internal/core/leads", json=body, headers=h)
        assert (first.status_code, second.status_code) == (201, 200)
        reuse = client.post("/internal/core/leads", json=body | {"city": "X"}, headers=h)
        assert reuse.status_code == 422
        lead_id = first.json()["lead_id"]
        url = f"/internal/core/leads/{lead_id}/status"
        stale = client.patch(url, json={"stage": "qualified", "expected_version": 9})
        assert stale.status_code == 409
        assert stale.json()["current"]["stage"] == "discovered"
        ok = client.patch(url, json={"stage": "qualified", "expected_version": 1})
        assert ok.status_code == 200
        bad = client.patch(url, json={"stage": "won", "expected_version": 2})
        assert bad.status_code == 422
        assert (
            client.patch(
                f"/internal/core/leads/{uuid4()}/status",
                json={"stage": "qualified", "expected_version": 1},
            ).status_code
            == 404
        )
        listing = client.get(
            "/internal/core/leads?state=hot&city=Cali&segment=62&min_score=70&service_line=saas"
        )
        assert listing.status_code == 200
        assert repo.kw["stage"] == "hot"
        assert repo.kw["min_score"] == 70
        assert client.get("/internal/core/leads?state=nope").status_code == 422
        one = client.get(f"/internal/core/leads/{uuid4()}")
        assert one.headers["ETag"] == '"4"'
        assert client.get("/internal/core/pipeline/kanban").json()["columns"]["won"] == []
    finally:
        main.app.dependency_overrides.clear()
