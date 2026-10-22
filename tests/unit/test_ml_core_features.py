"""The ML client of core's score features (T-165, INV-01, REQ-SEC-005)."""

from uuid import uuid4

import httpx
import pytest

from libs.common.internal_auth import verify_service_token
from libs.common.settings import get_settings
from services.ml.app.core_features import CoreFeaturesUnavailable, HttpCoreFeatures

TENANT = uuid4()
COMPANY = uuid4()


def _client(handler) -> HttpCoreFeatures:
    client = HttpCoreFeatures("http://core", get_settings().internal_service_token)
    client._client = httpx.AsyncClient(
        base_url="http://core", transport=httpx.MockTransport(handler)
    )
    return client


async def test_sends_signed_ml_token_and_only_the_asked_tenant() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "companies": [
                    {
                        "company_id": str(COMPANY),
                        "icp_fit": 0.7,
                        "signal_strength": 0.5,
                        "territory_opportunity": 1.0,
                        "source_version": "demo-1",
                    }
                ],
                "next_after": str(COMPANY),
            },
        )

    page = await _client(handler).get_page(TENANT, 50, None)
    request = seen[0]
    assert request.url.path == "/internal/core/admin/score-features"
    assert request.headers.get_list("x-tenant-id") == [str(TENANT)]
    assert request.url.params["limit"] == "50" and "after" not in request.url.params
    caller = verify_service_token(
        request.headers["x-service-token"], get_settings().internal_service_token
    )
    assert caller == "ml"
    assert page.companies[0].company_id == COMPANY and page.next_after == COMPANY


async def test_after_cursor_is_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"companies": [], "next_after": None})

    await _client(handler).get_page(TENANT, 5, COMPANY)
    assert seen[0].url.params["after"] == str(COMPANY)


@pytest.mark.parametrize("status", [500, 503, 401, 422])
async def test_error_status_is_unavailable(status: int) -> None:
    client = _client(lambda _r: httpx.Response(status))
    with pytest.raises(CoreFeaturesUnavailable):
        await client.get_page(TENANT, 5, None)


async def test_transport_error_and_bad_body_are_unavailable() -> None:
    def boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(CoreFeaturesUnavailable):
        await _client(boom).get_page(TENANT, 5, None)
    with pytest.raises(CoreFeaturesUnavailable):
        await _client(lambda _r: httpx.Response(200, json={"x": 1})).get_page(TENANT, 5, None)
    with pytest.raises(CoreFeaturesUnavailable):
        await _client(lambda _r: httpx.Response(200, content=b"nope")).get_page(TENANT, 5, None)
