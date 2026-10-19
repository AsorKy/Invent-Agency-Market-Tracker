"""Unit tests for the Brief lead route, tenant mapping, and optional summarizer wiring.

Fakes replace Redis, core, and the summarizer transport; the real FastAPI routes run through ASGI.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
from services.brief.app import main as brief_main  # noqa: E402
from services.brief.app.core_source import CoreNotFound, HttpCoreSource  # noqa: E402
from services.brief.app.repository import BriefRepository, TenantRequired  # noqa: E402
from services.brief.app.summarizer import HttpSummarizer, build_summarizer  # noqa: E402
from tests.unit.support_brief import COMPANY, TENANT, FakeCore, FakeRedis  # noqa: E402

HEADERS = {"X-Service-Token": "test-internal-token", "X-Tenant-Id": str(TENANT)}
LEAD = uuid4()


class LeadCore(FakeCore):
    """FakeCore that also resolves leads to companies."""

    async def get_lead_company(self, lead_id: UUID, tenant_id: UUID | None) -> UUID:
        if lead_id != LEAD:
            raise CoreNotFound("lead")
        return COMPANY


class NoTenantRepository(BriefRepository):
    async def get_latest(self, company_id: UUID, tenant_id: UUID | None = None) -> Any:
        raise TenantRequired("tenant needed")


def _install(
    core: Any, repository: Any = None, summarizer: Any = None
) -> brief_main.BriefState:
    state = brief_main.BriefState(
        repository or BriefRepository(None),
        FakeRedis(),  # type: ignore[arg-type]
        core,
        summarizer,
    )
    brief_main.app.state.brief = state
    return state


async def _get(url: str, headers: dict[str, str] = HEADERS) -> Any:
    transport = httpx.ASGITransport(app=brief_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url, headers=headers)


async def test_lead_digest_resolves_the_company() -> None:
    _install(LeadCore())
    response = await _get(f"/internal/brief/leads/{LEAD}/digest")
    assert response.status_code == 200
    body = response.json()
    assert body["lead_id"] == str(LEAD)
    assert body["company_id"] == str(COMPANY)


async def test_unknown_lead_is_404() -> None:
    _install(LeadCore())
    response = await _get(f"/internal/brief/leads/{uuid4()}/digest")
    assert response.status_code == 404


async def test_missing_tenant_is_a_client_error() -> None:
    _install(LeadCore(), NoTenantRepository(None))
    response = await _get(
        f"/internal/brief/companies/{COMPANY}/digest",
        {"X-Service-Token": "test-internal-token"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "tenant_required"


async def test_failing_configured_summarizer_degrades() -> None:
    def handler(request: Any) -> Any:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    _install(LeadCore(), summarizer=HttpSummarizer("http://summarizer/x", client=client))
    response = await _get(f"/internal/brief/companies/{COMPANY}/digest")
    assert response.status_code == 200
    assert response.json()["degraded"] is True
    assert response.headers["X-Brief-Cache"] == "degraded"


async def test_working_configured_summarizer_is_not_degraded() -> None:
    def handler(request: Any) -> Any:
        return httpx.Response(
            200,
            json={
                "version": "llm-1",
                "content": {
                    "headline": "h",
                    "alerts": [],
                    "next_steps": [],
                    "coverage": {},
                    "activity": [],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    _install(LeadCore(), summarizer=HttpSummarizer("http://summarizer/x", client=client))
    response = await _get(f"/internal/brief/companies/{COMPANY}/digest")
    assert response.json()["degraded"] is False
    assert response.json()["summarizer_version"] == "llm-1"


def test_build_summarizer_is_optional() -> None:
    assert build_summarizer({}) is None
    configured = build_summarizer(
        {"BRIEF_SUMMARIZER_URL": "http://s/x", "BRIEF_SUMMARIZER_TIMEOUT_SECONDS": "1.5"}
    )
    assert isinstance(configured, HttpSummarizer)


async def test_http_core_source_resolves_lead_company() -> None:
    def handler(request: Any) -> Any:
        assert request.url.path == f"/internal/core/leads/{LEAD}"
        return httpx.Response(200, json={"id": str(LEAD), "company_id": str(COMPANY)})

    source = HttpCoreSource("http://core", "t")
    source._client = httpx.AsyncClient(
        base_url="http://core", transport=httpx.MockTransport(handler)
    )
    assert await source.get_lead_company(LEAD, TENANT) == COMPANY
    await source.close()
