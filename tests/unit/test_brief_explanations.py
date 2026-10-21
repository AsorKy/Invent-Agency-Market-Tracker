"""Unit tests for Brief drivers and counterfactuals (REQ-LNS-007).

Fakes replace Redis, core, and the ml explanation port; no network is used.
"""

from typing import Any
from uuid import UUID

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
import httpx  # noqa: E402

from services.brief.app import main as brief_main  # noqa: E402
from services.brief.app.explanations import (  # noqa: E402
    HttpExplanationSource,
    normalize_explanation,
)
from services.brief.app.repository import BriefRepository  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402
from tests.unit.support_brief import COMPANY, TENANT, FakeCore, FakeRedis  # noqa: E402

pytestmark = pytest.mark.timeout(120)

HEADERS = service_headers("gateway", **{"X-Tenant-Id": str(TENANT)})
URL = f"/internal/brief/companies/{COMPANY}/digest"
PAYLOAD: dict[str, Any] = {
    "drivers": [{"feature": "recency", "contribution": 1.5, "direction": "up"}],
    "counterfactual": {"found": True, "changes": {}, "steps": 1, "score": 0.7},
}


class FakeExplanations:
    def __init__(self, result: dict[str, Any] | None = None, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls: list[tuple[UUID, UUID | None]] = []

    async def get_explanation(
        self, company_id: UUID, tenant_id: UUID | None
    ) -> dict[str, Any] | None:
        self.calls.append((company_id, tenant_id))
        if self.fail:
            raise RuntimeError("ml down")
        return self.result


async def _get(explanations: FakeExplanations | None) -> httpx.Response:
    brief_main.app.state.brief = brief_main.BriefState(
        BriefRepository(None),
        FakeRedis(),  # type: ignore[arg-type]
        FakeCore(),
        explanations=explanations,
    )
    transport = httpx.ASGITransport(app=brief_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(URL, headers=HEADERS)


async def test_regeneration_attaches_drivers_and_counterfactual() -> None:
    fake = FakeExplanations(PAYLOAD)
    response = await _get(fake)
    body = response.json()
    assert response.status_code == 200
    assert body["drivers"] == PAYLOAD["drivers"]
    assert body["counterfactual"] == PAYLOAD["counterfactual"]
    assert fake.calls == [(COMPANY, TENANT)]


@pytest.mark.parametrize("fake", [None, FakeExplanations(None), FakeExplanations(fail=True)])
async def test_missing_or_failing_explanation_keeps_brief_valid(
    fake: FakeExplanations | None,
) -> None:
    response = await _get(fake)
    body = response.json()
    assert response.status_code == 200
    assert body["drivers"] is None
    assert body["counterfactual"] is None
    assert body["content"]["headline"]


def test_normalize_drops_malformed_payloads() -> None:
    assert normalize_explanation("x") is None
    assert normalize_explanation({"drivers": "bad", "counterfactual": 3}) is None
    assert normalize_explanation(PAYLOAD) == PAYLOAD


async def test_http_source_returns_none_on_errors_and_parses_ok() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("404"):
            return httpx.Response(404)
        return httpx.Response(200, json=PAYLOAD)

    source = HttpExplanationSource("http://ml", "secret")
    source._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ml")
    assert await source.get_explanation(COMPANY, TENANT) == PAYLOAD
    assert "x-tenant-id" in seen[0].headers
    assert any(k.lower().startswith("x-") for k in seen[0].headers)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    source._client = httpx.AsyncClient(transport=httpx.MockTransport(boom), base_url="http://ml")
    assert await source.get_explanation(COMPANY, TENANT) is None
    await source.close()
