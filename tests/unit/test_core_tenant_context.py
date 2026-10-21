"""Core sets the RLS tenant context before it touches tenant-owned rows.

``core.company`` allows a row only when its tenant is null or equals ``app.tenant_id``. The
company transaction and the signal insert must therefore set that setting from the tenant that
ingest sends, or an insert with a tenant fails the policy and its outbox event loses the tenant
(the black-box e2e showed HTTP 500 and ``missing_tenant_id``). It relates to
``services/core/app/repository.py`` and ``libs/common/tenant.py``.
"""

from uuid import UUID

import pytest
from support_cov_services import FakeEngine

from libs.contracts.api import SignalCreate
from services.core.app.repository import SqlCompanyUnitOfWork

TENANT = UUID("00000000-0000-4000-8000-0000000000a1")


def _tenant_calls(engine: FakeEngine) -> list[dict[str, str]]:
    return [call[1] for call in engine.connection.calls if "set_config('app.tenant_id'" in call[0]]


async def test_company_transaction_sets_the_tenant_first() -> None:
    engine = FakeEngine()
    async with SqlCompanyUnitOfWork(engine, TENANT):  # type: ignore[arg-type]
        assert _tenant_calls(engine) == [{"tenant_id": str(TENANT)}]


async def test_company_transaction_without_tenant_sets_nothing() -> None:
    engine = FakeEngine()
    async with SqlCompanyUnitOfWork(engine):  # type: ignore[arg-type]
        pass
    assert _tenant_calls(engine) == []


async def test_a_failing_tenant_setup_closes_the_transaction() -> None:
    engine = FakeEngine()

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("set_config failed")

    engine.connection.execute = refuse  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="set_config failed"):
        async with SqlCompanyUnitOfWork(engine, TENANT):  # type: ignore[arg-type]
            pytest.fail("the body must not run")


def test_signal_contract_accepts_an_optional_tenant() -> None:
    body = {
        "company_id": "3c27bd76-6717-40dd-ba37-4d1a8848c71d",
        "source": "maps",
        "signal_type": "new_branch",
        "observed_at": "2026-10-19T00:00:00Z",
    }
    assert SignalCreate(**body).tenant_id is None
    assert SignalCreate(**body, tenant_id=str(TENANT)).tenant_id == TENANT  # type: ignore[arg-type]
