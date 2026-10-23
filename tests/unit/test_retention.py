"""Unit tests for retention, opt-out, and personal data guards (REQ-CMP-001, REQ-QA-002).

A fake unit of work models companies, signals, and dependants so the purge window, dry run,
cascade, idempotent opt-out, hashed audit entries, and the guards can be checked without a
database.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic")
from services.core.app.retention import (  # noqa: E402
    OptOutRequest,
    PersonalDataError,
    PurgeBlindError,
    ensure_public_role,
    opt_out,
    purge_inactive_companies,
    purge_tenants,
    reject_personal_identifiers,
    retention_cutoff,
)
from tests.unit.service_auth import service_headers  # noqa: E402

NOW = datetime(2026, 10, 18, tzinfo=UTC)


class Store:
    def __init__(self) -> None:
        self.companies: dict[UUID, dict[str, Any]] = {}
        self.signals: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.fail_on_delete = False
        self.tenanted = False

    def add(
        self,
        nit: str,
        last_signal_days: int | None,
        created_days: int = 800,
        tenant: UUID | None = None,
    ) -> UUID:
        company_id = uuid4()
        self.companies[company_id] = {
            "tenant": tenant,
            "nit": nit,
            "created_at": NOW - timedelta(days=created_days),
        }
        if last_signal_days is not None:
            self.signals.append(
                {"company_id": company_id, "observed_at": NOW - timedelta(days=last_signal_days)}
            )
        return company_id


class FakeUow:
    def __init__(self, store: Store, tenant: UUID | None = None) -> None:
        self.s = store
        self.tenant = tenant

    def _visible(self, row: dict[str, Any]) -> bool:
        # Models row level security: no context sees nothing when tenants are in use.
        return not self.s.tenanted or row["tenant"] == self.tenant

    async def __aenter__(self) -> "FakeUow":
        self.snap = (dict(self.s.companies), list(self.s.signals), list(self.s.audit))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.s.companies, self.s.signals, self.s.audit = self.snap

    async def find_purge_candidates(self, cutoff: datetime, limit: int) -> list[UUID]:
        found = []
        for company_id, row in self.s.companies.items():
            if not self._visible(row):
                continue
            seen = [s["observed_at"] for s in self.s.signals if s["company_id"] == company_id]
            if (max(seen) if seen else row["created_at"]) < cutoff:
                found.append(company_id)
        return found[:limit]

    async def find_company(self, company_id: UUID | None, nit: str | None) -> UUID | None:
        for cid, row in self.s.companies.items():
            if self._visible(row) and (cid == company_id or (nit and row["nit"] == nit)):
                return cid
        return None

    async def delete_company_data(self, company_id: UUID) -> dict[str, int]:
        if self.s.fail_on_delete:
            raise RuntimeError("boom")
        if company_id not in self.s.companies or not self._visible(self.s.companies[company_id]):
            return {"signal": 0, "company": 0}
        before = len(self.s.signals)
        self.s.signals = [s for s in self.s.signals if s["company_id"] != company_id]
        del self.s.companies[company_id]
        return {"signal": before - len(self.s.signals), "company": 1}

    async def add_audit(self, *, action: str, payload: dict[str, Any]) -> None:
        self.s.audit.append({"action": action, "payload": payload})


def _purge(store: Store, **kw: Any) -> dict[str, Any]:
    return asyncio.run(purge_inactive_companies(FakeUow(store), now=NOW, **kw))


def test_cutoff_is_twelve_months_back() -> None:
    assert retention_cutoff(NOW) == NOW - timedelta(days=365)


def test_purge_removes_only_companies_inactive_for_a_year() -> None:
    store = Store()
    old = store.add("1", last_signal_days=400)
    recent = store.add("2", last_signal_days=30)
    mixed = store.add("3", last_signal_days=500)
    store.signals.append({"company_id": mixed, "observed_at": NOW - timedelta(days=10)})
    never_new = store.add("4", last_signal_days=None, created_days=10)
    never_old = store.add("5", last_signal_days=None, created_days=500)
    result = _purge(store)
    assert result["purged"] == 2 and result["candidates"] == 2
    assert set(store.companies) == {recent, mixed, never_new}
    assert old not in store.companies and never_old not in store.companies
    assert result["deleted"]["company"] == 2 and result["deleted"]["signal"] == 1
    (entry,) = store.audit
    assert entry["action"] == "retention.purge" and entry["payload"]["companies"] == 2


def test_dry_run_changes_nothing_and_second_run_is_idempotent() -> None:
    store = Store()
    store.add("1", last_signal_days=400)
    dry = _purge(store, dry_run=True)
    assert dry["dry_run"] and dry["candidates"] == 1 and dry["purged"] == 0
    assert len(store.companies) == 1 and store.audit == []
    assert _purge(store)["purged"] == 1
    again = _purge(store)
    assert again["purged"] == 0 and len(store.audit) == 1


def test_limit_bounds_a_single_run_and_failure_rolls_back() -> None:
    store = Store()
    for i in range(3):
        store.add(str(i), last_signal_days=400)
    assert _purge(store, limit=2)["purged"] == 2
    assert len(store.companies) == 1
    store.fail_on_delete = True
    with pytest.raises(RuntimeError):
        _purge(store)
    assert len(store.companies) == 1


def test_opt_out_by_id_and_nit_is_idempotent_and_audit_is_hashed() -> None:
    store = Store()
    by_id = store.add("9001", last_signal_days=1)
    store.add("9002", last_signal_days=1)

    def go(**kw: Any) -> dict[str, Any]:
        return asyncio.run(opt_out(FakeUow(store), OptOutRequest(**kw)))

    first = go(company_id=by_id)
    assert first["status"] == "removed" and by_id not in store.companies
    assert go(company_id=by_id)["status"] == "already_removed"
    assert go(nit="900.2")["status"] == "removed"
    assert store.companies == {}
    assert go(nit="900.2")["status"] == "already_removed"
    assert len(store.audit) == 2
    dumped = json.dumps(store.audit)
    assert "9002" not in dumped and str(by_id) not in dumped
    assert len(store.audit[0]["payload"]["reference_sha256"]) == 64


def test_opt_out_requires_an_identifier() -> None:
    with pytest.raises(ValueError):
        OptOutRequest()
    with pytest.raises(ValueError):
        OptOutRequest(nit="--")


@pytest.mark.parametrize(
    "data",
    [
        {"cedula": "123"},
        {"person": {"Document_Number": "1"}},
        {"items": [{"home-address": "Calle 1"}]},
        {"note": "CC 1.234.567.890"},
        {"note": "Cedula: 79123456"},
        {"nested": ["c.c. 80 123 456"]},
        {"personal_phone": "3001234567"},
    ],
)
def test_guard_rejects_personal_identifiers(data: dict[str, Any]) -> None:
    with pytest.raises(PersonalDataError):
        reject_personal_identifiers(data)


def test_guard_allows_business_data_and_never_echoes_values() -> None:
    reject_personal_identifiers(
        {
            "legal_name": "Acme SAS",
            "nit": "900.123.456-7",
            "website": "https://acme.co",
            "payload": {"hiring": ["CTO", "Gerente comercial"], "note": "cc: sales team"},
        }
    )
    with pytest.raises(PersonalDataError) as error:
        reject_personal_identifiers({"note": "CC 1.234.567.890"})
    assert "1.234" not in str(error.value)


def test_only_public_business_roles_are_accepted() -> None:
    assert ensure_public_role("  CEO ") == "ceo"
    assert ensure_public_role("Gerente Comercial") == "gerente comercial"
    for bad in ("", None, "vecino", "madre de familia"):
        with pytest.raises(PersonalDataError):
            ensure_public_role(bad)


def test_routes_apply_guard_and_compliance_endpoints() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from services.core.app import main

    store = Store()
    store.add("9001", last_signal_days=400)

    class Repo:
        def retention_unit_of_work(self, tenant_id: UUID | None = None) -> FakeUow:
            return FakeUow(store, tenant_id)

    async def fake_tenants(*_args: Any, **_kwargs: Any) -> list[UUID]:
        return [uuid4()]

    main.list_tenant_ids = fake_tenants
    main.app.state.repository = Repo()
    client = TestClient(main.app, headers=service_headers())
    try:
        bad_signal = {
            "company_id": str(uuid4()),
            "source": "maps",
            "signal_type": "listing",
            "observed_at": "2026-01-01T00:00:00Z",
            "payload": {"cedula": "123"},
        }
        rejected = client.post("/internal/core/signals", json=bad_signal)
        assert rejected.status_code == 422 and "cedula" in rejected.json()["detail"]
        company = client.post(
            "/internal/core/companies/upsert",
            json={"legal_name": "Acme", "source_ids": {"home_address": "x"}},
        )
        assert company.status_code == 422
        dry = client.post("/internal/core/compliance/purge")
        assert dry.status_code == 200 and dry.json()["dry_run"] and len(store.companies) == 1
        real = client.post("/internal/core/compliance/purge?dry_run=false")
        assert real.json()["purged"] == 1 and store.companies == {}
        tenant = {"X-Tenant-Id": str(uuid4())}
        gone = client.post(
            "/internal/core/compliance/opt-out", json={"nit": "9001"}, headers=tenant
        )
        assert gone.json()["status"] == "already_removed"
        assert (
            client.post("/internal/core/compliance/opt-out", json={}, headers=tenant).status_code
            == 422
        )
        no_tenant = client.post("/internal/core/compliance/opt-out", json={"nit": "9001"})
        assert no_tenant.status_code == 400 and no_tenant.json()["detail"] == "tenant_required"
    finally:
        main.app.dependency_overrides.clear()


def test_tenant_opt_out_deletes_only_its_own_company_and_other_tenant_is_not_found() -> None:
    store = Store()
    store.tenanted = True
    a, b = uuid4(), uuid4()
    in_a = store.add("9001", last_signal_days=1, tenant=a)
    in_b = store.add("9001", last_signal_days=1, tenant=b)  # same NIT, other tenant

    def go(tenant: UUID | None, **kw: Any) -> dict[str, Any]:
        return asyncio.run(opt_out(FakeUow(store, tenant), OptOutRequest(**kw)))

    assert go(a, nit="9001")["status"] == "removed"
    assert in_a not in store.companies and in_b in store.companies
    assert go(a, company_id=in_b)["status"] == "already_removed"  # not found, never deleted
    assert go(None, nit="9001")["status"] == "already_removed"  # no context sees nothing
    assert in_b in store.companies
    assert go(b, nit="9001")["status"] == "removed"
    assert all(e["payload"].get("reference_sha256") for e in store.audit)


def test_purge_tenants_purges_each_tenant_separately_and_one_failure_does_not_stop_others() -> None:
    store = Store()
    store.tenanted = True
    a, b, c = uuid4(), uuid4(), uuid4()
    old_a = store.add("1", last_signal_days=400, tenant=a)
    old_b = store.add("2", last_signal_days=400, tenant=b)
    old_c = store.add("3", last_signal_days=400, tenant=c)
    store.add("4", last_signal_days=1, tenant=a)
    seen: list[UUID] = []

    class Failing(FakeUow):
        async def delete_company_data(self, company_id: UUID) -> dict[str, int]:
            if self.tenant == b:
                raise RuntimeError("boom")
            return await super().delete_company_data(company_id)

    def factory(tenant: UUID) -> FakeUow:
        seen.append(tenant)
        return Failing(store, tenant)

    result = asyncio.run(purge_tenants(factory, [a, b, c], now=NOW))
    assert seen == [a, b, c]
    assert result["failed"] == [str(b)] and result["purged"] == 2
    assert result["per_tenant"][str(a)]["purged"] == 1
    assert old_a not in store.companies and old_c not in store.companies
    assert old_b in store.companies  # rolled back, not purged
    assert asyncio.run(purge_tenants(factory, []))["tenants"] == 0


def test_purge_that_deletes_zero_rows_for_an_existing_company_fails() -> None:
    store = Store()
    cid = store.add("1", last_signal_days=400)

    class Blind(FakeUow):
        async def delete_company_data(self, company_id: UUID) -> dict[str, int]:
            return {"signal": 0, "company": 0}

    with pytest.raises(PurgeBlindError):
        asyncio.run(purge_inactive_companies(Blind(store), now=NOW))
    assert cid in store.companies

    class BlindOptOut(Blind):
        pass

    with pytest.raises(PurgeBlindError):
        asyncio.run(opt_out(BlindOptOut(store), OptOutRequest(company_id=cid)))


def test_identity_client_sends_a_core_token_and_reads_ids_only() -> None:
    import httpx

    from libs.common.internal_auth import verify_service_token
    from services.core.app.identity_tenants import list_tenant_ids

    ids = [uuid4(), uuid4()]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"tenant_ids": [str(i) for i in ids]})

    async def go(h: Any) -> list[UUID]:
        async with httpx.AsyncClient(
            base_url="http://identity", transport=httpx.MockTransport(h)
        ) as client:
            return await list_tenant_ids("http://identity", "secret", client=client)

    assert asyncio.run(go(handler)) == ids
    assert calls[0].url.path == "/internal/identity/tenants"
    assert verify_service_token(calls[0].headers["X-Service-Token"], "secret") == "core"
    with pytest.raises(RuntimeError):
        asyncio.run(go(lambda r: httpx.Response(401, json={})))
