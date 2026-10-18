"""Unit tests for tenant configuration (REQ-COR-004, REQ-EXT-002, REQ-QA-002).

A fake unit of work stages writes and restores state on rollback. Proves ETag concurrency (412 on
a stale version), history of the previous version, ``tenant.config_updated`` in the same
transaction, rejection of unknown keys, and acceptance of the reserved ``funnel_rules`` keys.
"""

import asyncio
from copy import deepcopy
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic")
from pydantic import ValidationError  # noqa: E402

from services.core.app.tenant_config import (  # noqa: E402
    StaleConfigError,
    TenantConfig,
    config_to_row,
    etag,
    parse_etag,
    read_config,
    row_to_config,
    update_config,
)

TENANT = uuid4()


class Store:
    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []
        self.log: list[str] = []
        self.fail_on: str | None = None


class FakeUow:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def __aenter__(self) -> "FakeUow":
        self.s.log.append("begin")
        self.snap = deepcopy((self.s.rows, self.s.history, self.s.outbox))
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
            self.s.rows, self.s.history, self.s.outbox = self.snap
            self.s.log.append("rollback")

    def _step(self, name: str) -> None:
        self.s.log.append(name)
        if self.s.fail_on == name:
            raise RuntimeError(f"boom in {name}")

    async def get_config(self, tenant_id: UUID) -> dict[str, Any] | None:
        row = self.s.rows.get(tenant_id)
        return deepcopy(row) if row else None

    async def insert_config(self, row: dict[str, Any]) -> None:
        self._step("insert")
        self.s.rows[row["tenant_id"]] = deepcopy(row)

    async def close_version(self, old: dict[str, Any], closed_at: Any) -> None:
        self._step("history")
        self.s.history.append(deepcopy(old) | {"valid_to": closed_at})

    async def update_config(self, row: dict[str, Any]) -> None:
        self._step("update")
        self.s.rows[row["tenant_id"]] = deepcopy(row)

    async def add_outbox_event(self, **event: Any) -> None:
        self._step("outbox")
        self.s.outbox.append(event)


def _put(store: Store, config: TenantConfig, if_match: int, **kw: Any) -> dict[str, Any]:
    return asyncio.run(update_config(FakeUow(store), TENANT, config, if_match=if_match, **kw))


def test_first_write_creates_version_one_with_event() -> None:
    store = Store()
    assert asyncio.run(read_config(FakeUow(store), TENANT))["version"] == 0
    body = _put(store, TenantConfig(bright_data_budget_usd=100), 0, request_id="r")
    assert body["version"] == 1 and body["bright_data_budget_usd"] == 100
    assert store.log[-4:] == ["begin", "insert", "outbox", "commit"]
    (event,) = store.outbox
    assert event["payload"]["version"] == 1 and event["payload"]["request_id"] == "r"
    assert store.history == []


def test_update_archives_previous_version() -> None:
    store = Store()
    _put(store, TenantConfig(bright_data_budget_usd=100), 0)
    body = _put(store, TenantConfig(bright_data_budget_usd=250), 1)
    assert body["version"] == 2
    (old,) = store.history
    assert old["version"] == 1 and old["bright_data_budget_usd"] == 100
    assert store.rows[TENANT]["version"] == 2
    assert len(store.outbox) == 2


def test_stale_etag_is_rejected_without_writes() -> None:
    store = Store()
    _put(store, TenantConfig(), 0)
    _put(store, TenantConfig(bright_data_budget_usd=1), 1)
    with pytest.raises(StaleConfigError) as stale:
        _put(store, TenantConfig(bright_data_budget_usd=2), 1)
    assert stale.value.current_version == 2
    with pytest.raises(StaleConfigError):
        _put(store, TenantConfig(), 0)
    assert store.rows[TENANT]["bright_data_budget_usd"] == 1
    assert len(store.outbox) == 2 and len(store.history) == 1


def test_identical_put_changes_nothing() -> None:
    store = Store()
    first = _put(store, TenantConfig(), 0)
    again = _put(store, TenantConfig(), 1)
    assert again == first
    assert len(store.outbox) == 1


def test_failure_rolls_back_config_history_and_event() -> None:
    store = Store()
    _put(store, TenantConfig(), 0)
    store.fail_on = "outbox"
    with pytest.raises(RuntimeError):
        _put(store, TenantConfig(bright_data_budget_usd=5), 1)
    assert store.rows[TENANT]["version"] == 1
    assert store.history == [] and len(store.outbox) == 1


def test_unknown_keys_rejected_and_reserved_keys_accepted() -> None:
    with pytest.raises(ValidationError):
        TenantConfig.model_validate({"mystery": 1})
    with pytest.raises(ValidationError):
        TenantConfig.model_validate({"brief_rules": {"mystery": 1}})
    with pytest.raises(ValidationError):
        TenantConfig.model_validate({"bright_data_budget_usd": -1})
    config = TenantConfig.model_validate(
        {"funnel_rules": {"gates": ["G1"], "geo_overrides": {"Medellin": {"min_score": 60}}}}
    )
    assert config.funnel_rules is not None
    assert config.funnel_rules.geo_overrides["Medellin"]["min_score"] == 60
    store = Store()
    _put(store, config, 0)
    assert store.rows[TENANT]["metadata"]["funnel_rules"]["gates"] == ["G1"]
    assert row_to_config(store.rows[TENANT]) == config
    assert config_to_row(TenantConfig())["metadata"] == {}


def test_brief_rules_defaults_follow_the_adr() -> None:
    rules = TenantConfig().brief_rules
    assert rules.alert_rules.no_contact_days_warning == 5
    assert rules.alert_rules.min_score_for_hot == 70
    assert rules.disabled_alert_codes == []


def test_etag_helpers() -> None:
    assert etag(3) == '"3"'
    assert parse_etag('"3"') == 3 and parse_etag('W/"4"') == 4 and parse_etag("5") == 5
    with pytest.raises(ValueError):
        parse_etag('"abc"')


def test_routes() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from services.core.app import main

    store = Store()

    class Repo:
        def config_unit_of_work(self) -> FakeUow:
            return FakeUow(store)

    main.app.state.repository = Repo()
    main.app.dependency_overrides[main.require_internal_token] = lambda: None
    client = TestClient(main.app)
    url = "/internal/core/admin/tenant-config"
    h = {"X-Tenant-ID": str(TENANT)}
    try:
        got = client.get(url, headers=h)
        assert got.status_code == 200 and got.headers["ETag"] == '"0"'
        assert client.get(url).status_code == 422
        assert client.put(url, json={}, headers=h).status_code == 428
        assert client.put(url, json={}, headers=h | {"If-Match": "x"}).status_code == 400
        bad = client.put(url, json={"zzz": 1}, headers=h | {"If-Match": '"0"'})
        assert bad.status_code == 422
        ok = client.put(url, json={"bright_data_budget_usd": 9}, headers=h | {"If-Match": '"0"'})
        assert ok.status_code == 200 and ok.headers["ETag"] == '"1"'
        stale = client.put(url, json={}, headers=h | {"If-Match": '"0"'})
        assert stale.status_code == 412
        assert stale.json()["detail"]["current_version"] == 1
        assert client.get(url, headers=h).json()["bright_data_budget_usd"] == 9
    finally:
        main.app.dependency_overrides.clear()
