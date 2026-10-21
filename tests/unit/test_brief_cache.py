"""Unit tests for the Brief cache states and 503 mapping (REQ-BRF-001).

Fakes replace Redis and core; the real FastAPI routes run through ASGI.
"""

import asyncio

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from services.brief.app import main as brief_main  # noqa: E402
from services.brief.app.models import CompanyFacts  # noqa: E402
from services.brief.app.repository import BriefRepository  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402
from tests.unit.support_brief import COMPANY, TENANT, FakeCore, FakeRedis  # noqa: E402

HEADERS = service_headers("gateway", **{"X-Tenant-Id": str(TENANT)})
URL = f"/internal/brief/companies/{COMPANY}/digest"


def _install(core: FakeCore) -> brief_main.BriefState:
    state = brief_main.BriefState(
        BriefRepository(None),
        FakeRedis(),  # type: ignore[arg-type]
        core,
    )
    brief_main.app.state.brief = state
    return state


async def _get(core: FakeCore):  # type: ignore[no-untyped-def]
    import httpx

    transport = httpx.ASGITransport(app=brief_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(URL, headers=HEADERS)


async def test_first_read_generates_synchronously_and_hits_afterwards() -> None:
    core = FakeCore()
    _install(core)
    first = await _get(core)
    assert first.status_code == 200
    assert first.headers["X-Brief-Cache"] == "hit"
    assert first.json()["stale"] is False
    assert first.json()["source_version"] == 1
    second = await _get(core)
    assert second.headers["X-Brief-Cache"] == "hit"
    assert second.json()["generated_at"] == first.json()["generated_at"]


async def test_outdated_digest_is_served_stale_then_regenerated() -> None:
    core = FakeCore()
    state = _install(core)
    first = await _get(core)
    core.facts = CompanyFacts(company_id=COMPANY, version=1, signal_versions_sum=2)
    stale = await _get(core)
    assert stale.headers["X-Brief-Cache"] == "stale"
    assert stale.json()["stale"] is True
    assert stale.json()["source_version"] == first.json()["source_version"]
    # Background regeneration ran after the response with ASGITransport.
    await asyncio.sleep(0)
    latest = await state.repository.get_latest(COMPANY)
    assert latest is not None and latest.source_version == 3
    fresh = await _get(core)
    assert fresh.headers["X-Brief-Cache"] == "hit"


async def test_tenant_config_update_invalidates_lazily() -> None:
    core = FakeCore()
    _install(core)
    await _get(core)
    core.config_version = 2
    response = await _get(core)
    assert response.headers["X-Brief-Cache"] == "stale"
    await asyncio.sleep(0)
    assert (await _get(core)).headers["X-Brief-Cache"] == "hit"


async def test_degraded_digest_reports_degraded_header() -> None:
    core = FakeCore()
    state = _install(core)
    await _get(core)
    stored = await state.repository.get_latest(COMPANY)
    assert stored is not None
    await state.repository.save(stored.model_copy(update={"degraded": True}))
    response = await _get(core)
    assert response.headers["X-Brief-Cache"] == "degraded"
    assert response.json()["degraded"] is True


async def test_core_unreachable_returns_503_db_unavailable() -> None:
    core = FakeCore()
    core.down = True
    _install(core)
    response = await _get(core)
    assert response.status_code == 503
    assert response.json()["detail"] == "db_unavailable"


async def test_unknown_company_returns_404() -> None:
    core = FakeCore()
    core.missing = True
    _install(core)
    assert (await _get(core)).status_code == 404
