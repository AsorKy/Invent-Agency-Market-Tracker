"""Unit tests for the company upsert (REQ-COR-007, REQ-QA-002).

A fake unit of work stages writes and records the transaction boundary (begin, commit, rollback),
so the tests prove that the company row, the SCD2 history row, and the outbox event are written in
the same transaction and that repeating a payload writes nothing. No database is needed.
"""

import asyncio
from copy import deepcopy
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest

pytest.importorskip("pydantic")
from services.core.app.companies import (  # noqa: E402
    CompanyUpsert,
    company_slug,
    normalize_nit,
    upsert_company,
)


class FakeStore:
    """Committed state plus a log of every call, including transaction boundaries."""

    def __init__(self) -> None:
        self.companies: dict[UUID, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []
        self.log: list[str] = []
        self.fail_on: str | None = None

    def unit_of_work(self) -> "FakeUnitOfWork":
        return FakeUnitOfWork(self)


class FakeUnitOfWork:
    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.staged: dict[str, Any] = {}

    async def __aenter__(self) -> "FakeUnitOfWork":
        self.store.log.append("begin")
        self.staged = {
            "companies": deepcopy(self.store.companies),
            "history": list(self.store.history),
            "outbox": list(self.store.outbox),
        }
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.store.companies = self.staged["companies"]
            self.store.history = self.staged["history"]
            self.store.outbox = self.staged["outbox"]
            self.store.log.append("commit")
        else:
            self.store.log.append("rollback")

    def _step(self, name: str) -> None:
        self.store.log.append(name)
        if self.store.fail_on == name:
            raise RuntimeError(f"boom in {name}")

    async def lock_key(self, key: str) -> None:
        self._step("lock")

    async def find_company(self, nit: str | None, slug: str) -> dict[str, Any] | None:
        self._step("find")
        for row in self.staged["companies"].values():
            if (nit and row["nit"] == nit) or row["slug"] == slug:
                return deepcopy(row)
        return None

    async def insert_company(self, row: dict[str, Any]) -> None:
        self._step("insert")
        self.staged["companies"][row["id"]] = deepcopy(row)

    async def close_version(self, old: dict[str, Any], closed_at: Any) -> None:
        self._step("history")
        self.staged["history"].append(deepcopy(old) | {"valid_to": closed_at})

    async def update_company(self, row: dict[str, Any]) -> None:
        self._step("update")
        self.staged["companies"][row["id"]] = deepcopy(row)

    async def add_outbox_event(self, **event: Any) -> None:
        self._step("outbox")
        self.staged["outbox"].append(event)


def _run(store: FakeStore, payload: CompanyUpsert) -> dict[str, Any]:
    return asyncio.run(upsert_company(store.unit_of_work(), payload, request_id="r-1"))


def _payload(**overrides: Any) -> CompanyUpsert:
    base: dict[str, Any] = {
        "legal_name": "Acme SAS",
        "nit": "900.123.456-7",
        "city": "Bogota",
        "source_ids": {"rues": "123"},
    }
    return CompanyUpsert(**(base | overrides))


def test_create_writes_company_and_event_in_one_transaction() -> None:
    store = FakeStore()
    result = _run(store, _payload())
    assert result["status"] == "created"
    assert result["version"] == 1
    assert store.log == ["begin", "lock", "find", "insert", "outbox", "commit"]
    (company,) = store.companies.values()
    assert company["nit"] == "9001234567"
    assert company["slug"] == "nit-9001234567"
    (event,) = store.outbox
    assert event["payload"]["change"] == "created"
    assert event["payload"]["request_id"] == "r-1"
    assert event["version"] == 1
    assert store.history == []


def test_repeating_same_payload_changes_nothing() -> None:
    store = FakeStore()
    first = _run(store, _payload())
    snapshot = deepcopy(store.companies)
    again = _run(store, _payload(nit="9001234567"))
    assert again["status"] == "unchanged"
    assert again["company_id"] == first["company_id"]
    assert again["version"] == 1
    assert store.companies == snapshot
    assert len(store.outbox) == 1
    assert store.history == []
    assert store.log[-4:] == ["begin", "lock", "find", "commit"]


def test_change_bumps_version_keeps_history_and_emits_event() -> None:
    store = FakeStore()
    _run(store, _payload())
    result = _run(store, _payload(city="Medellin", employee_count=40))
    assert result["status"] == "updated"
    assert result["version"] == 2
    assert store.log[-7:] == ["begin", "lock", "find", "history", "update", "outbox", "commit"]
    (company,) = store.companies.values()
    assert company["city"] == "Medellin"
    assert company["employee_count"] == 40
    (old,) = store.history
    assert old["city"] == "Bogota"
    assert old["version"] == 1
    assert old["valid_to"] is not None
    event = store.outbox[-1]
    assert event["version"] == 2
    assert sorted(event["payload"]["changed_fields"]) == ["city", "employee_count"]
    assert len(store.outbox) == 2


def test_omitted_fields_keep_stored_values_and_source_ids_merge() -> None:
    store = FakeStore()
    _run(store, _payload(trade_name="Acme"))
    _run(store, CompanyUpsert(legal_name="Acme SAS", nit="9001234567", source_ids={"dian": "9"}))
    (company,) = store.companies.values()
    assert company["trade_name"] == "Acme"
    assert company["source_flags"]["source_ids"] == {"rues": "123", "dian": "9"}


def test_canonical_key_without_nit_is_stable_and_accent_insensitive() -> None:
    a = CompanyUpsert(legal_name="  Compa\u00f1\u00eda  Uno ", city="Bogot\u00e1", source_ids={"b": "2", "a": "1"})
    b = CompanyUpsert(legal_name="compania uno", city="bogota", source_ids={"a": "1", "b": "2"})
    assert company_slug(a) == company_slug(b)
    assert company_slug(a).startswith("ck-")
    store = FakeStore()
    assert _run(store, a)["status"] == "created"
    assert _run(store, a)["status"] == "unchanged"
    assert _run(store, b)["status"] == "updated"  # same identity, display name differs
    assert len(store.companies) == 1


def test_same_nit_with_new_name_updates_existing_company() -> None:
    store = FakeStore()
    created = _run(store, _payload())
    other = _run(store, _payload(legal_name="Acme Colombia SAS"))
    assert other["company_id"] == created["company_id"]
    assert other["status"] == "updated"


@pytest.mark.parametrize("failing", ["history", "update", "outbox"])
def test_failure_rolls_back_company_history_and_event(failing: str) -> None:
    store = FakeStore()
    _run(store, _payload())
    before = (deepcopy(store.companies), list(store.history), list(store.outbox))
    store.fail_on = failing
    with pytest.raises(RuntimeError):
        _run(store, _payload(city="Cali"))
    assert store.log[-1] == "rollback"
    assert (store.companies, store.history, store.outbox) == before


def test_normalize_nit() -> None:
    assert normalize_nit("900.123.456-7") == "9001234567"
    assert normalize_nit(" - ") is None
    assert normalize_nit(None) is None


def test_endpoint_requires_token_and_reports_status(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from libs.common.settings import get_settings
    from services.core.app import main

    store = FakeStore()

    class FakeRepository:
        def company_unit_of_work(self) -> FakeUnitOfWork:
            return store.unit_of_work()

    main.app.state.repository = FakeRepository()
    client = TestClient(main.app)
    body = {"legal_name": "Acme SAS", "nit": "900123456-7"}
    assert client.post("/internal/core/companies/upsert", json=body).status_code == 401
    headers = {"X-Service-Token": get_settings().internal_service_token}
    first = client.post("/internal/core/companies/upsert", json=body, headers=headers)
    assert first.status_code == 201
    assert first.json()["status"] == "created"
    second = client.post("/internal/core/companies/upsert", json=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["status"] == "unchanged"
    assert second.json()["version"] == 1
    bad = client.post("/internal/core/companies/upsert", json={"nit": "1"}, headers=headers)
    assert bad.status_code == 422
