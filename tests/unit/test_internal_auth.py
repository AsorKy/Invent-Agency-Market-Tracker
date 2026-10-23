"""Test signed service tokens and tenant header trust (REQ-SEC-005)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")
httpx = pytest.importorskip("httpx")
pytest.importorskip("structlog")
from fastapi import Depends, FastAPI, Header  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from libs.common.internal_auth import (  # noqa: E402
    caller_service,
    require_gateway_token,
    require_internal_token,
    require_service_token,
    service_token_header,
    sign_service_token,
    verify_service_token,
)
from libs.common.settings import get_settings  # noqa: E402

SECRET = "unit-test-secret"
TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _app() -> TestClient:
    app = FastAPI()

    @app.get("/internal/x", dependencies=[Depends(require_internal_token)])
    async def any_caller(x_tenant_id: str | None = Header(default=None)) -> dict[str, str | None]:
        return {"tenant": x_tenant_id}

    @app.get("/internal/gw", dependencies=[Depends(require_gateway_token)])
    async def gateway_only(x_tenant_id: str | None = Header(default=None)) -> dict[str, str | None]:
        return {"tenant": x_tenant_id}

    return TestClient(app)


def test_roundtrip_and_expiry() -> None:
    token = sign_service_token("gateway", SECRET, ttl_seconds=30, now=1000)
    assert verify_service_token(token, SECRET, now=1010) == "gateway"
    assert verify_service_token(token, SECRET, now=1031) is None


def test_tampered_or_foreign_tokens_rejected() -> None:
    token = sign_service_token("brief", SECRET, now=1000)
    forged = token.replace("brief", "gateway", 1)
    assert verify_service_token(forged, SECRET, now=1001) is None
    assert verify_service_token(token, "other-secret", now=1001) is None
    for junk in ("", "a.b", "v1.x.notdigits.sig", "v2.gateway.9999999999.00"):
        assert verify_service_token(junk, SECRET, now=1) is None


def test_tenant_header_without_token_is_refused() -> None:
    client = _app()
    assert client.get("/internal/x", headers={"X-Tenant-ID": TENANT}).status_code == 401
    assert client.get("/internal/gw", headers={"X-Tenant-ID": TENANT}).status_code == 401


def test_tenant_header_with_gateway_token_is_accepted() -> None:
    client = _app()
    headers = {
        "X-Tenant-ID": TENANT,
        "X-Service-Token": sign_service_token("gateway", SECRET),
    }
    response = client.get("/internal/gw", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"tenant": TENANT}


def test_non_gateway_and_expired_tokens_refused_on_gateway_route() -> None:
    client = _app()
    brief = sign_service_token("brief", SECRET)
    expired = sign_service_token("gateway", SECRET, ttl_seconds=-10)
    for token in (brief, expired, SECRET):
        headers = {"X-Tenant-ID": TENANT, "X-Service-Token": token}
        assert client.get("/internal/gw", headers=headers).status_code == 401
    assert client.get("/internal/x", headers={"X-Service-Token": expired}).status_code == 401


def test_raw_shared_secret_is_refused_everywhere() -> None:
    client = _app()
    headers = {"X-Service-Token": SECRET}
    assert client.get("/internal/x", headers=headers).status_code == 401
    assert client.get("/internal/gw", headers=headers).status_code == 401
    assert caller_service(SECRET) is None


def test_expired_token_is_refused_on_any_internal_route() -> None:
    client = _app()
    expired = sign_service_token("brief", SECRET, ttl_seconds=-1)
    assert client.get("/internal/x", headers={"X-Service-Token": expired}).status_code == 401


def test_service_token_header_mints_a_fresh_token_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr("libs.common.internal_auth.time.time", lambda: clock["now"])
    first = service_token_header("brief", SECRET)["X-Service-Token"]
    clock["now"] = 1100.0
    second = service_token_header("brief", SECRET)["X-Service-Token"]
    assert first != second
    assert verify_service_token(first, SECRET, now=1010) == "brief"
    assert verify_service_token(first, SECRET, now=1100) is None
    assert verify_service_token(second, SECRET, now=1110) == "brief"


def test_require_service_token_enforces_the_allow_list() -> None:
    app = FastAPI()

    @app.get("/internal/list", dependencies=[Depends(require_service_token("brief", "ingest"))])
    async def listed() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    for service, expected in (("brief", 200), ("ingest", 200), ("gateway", 401), ("seed", 401)):
        token = sign_service_token(service, SECRET)
        response = client.get("/internal/list", headers={"X-Service-Token": token})
        assert response.status_code == expected, service
    assert client.get("/internal/list").status_code == 401
    with pytest.raises(ValueError):
        require_service_token()


def test_core_refuses_tenant_header_without_token() -> None:
    from services.core.app.main import app

    response = TestClient(app).get("/internal/core/leads", headers={"X-Tenant-ID": TENANT})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "internal/core/leads",
        "internal/identity/me",
        "/internal/brief/leads/1",
        "internal",
        "api/v1/internal/core/leads",
    ],
)
def test_gateway_never_routes_internal(path: str) -> None:
    from services.gateway.app.routes import Route, resolve

    result = resolve(path, "GET", get_settings())
    assert not isinstance(result, Route)


def _seed_app() -> TestClient:
    app = FastAPI()

    @app.get(
        "/internal/seed",
        dependencies=[Depends(require_service_token("gateway", "seed", "verify_live"))],
    )
    async def listed() -> dict[str, str]:
        return {"ok": "1"}

    return TestClient(app)


@pytest.mark.parametrize("service", ["seed", "verify_live"])
def test_seed_and_verify_live_are_accepted_outside_production(
    monkeypatch: pytest.MonkeyPatch, service: str
) -> None:
    for environment in ("local", "ci"):
        monkeypatch.setenv("ENVIRONMENT", environment)
        monkeypatch.setenv("APP_PROFILE", "local")
        get_settings.cache_clear()
        response = _seed_app().get(
            "/internal/seed", headers={"X-Service-Token": sign_service_token(service, SECRET)}
        )
        assert response.status_code == 200, environment


@pytest.mark.parametrize("service", ["seed", "verify_live"])
@pytest.mark.parametrize(
    ("profile", "environment"),
    [("production", "local"), ("local", "production"), ("production", "production")],
)
def test_seed_and_verify_live_are_refused_in_production(
    monkeypatch: pytest.MonkeyPatch, service: str, profile: str, environment: str
) -> None:
    monkeypatch.setenv("APP_PROFILE", profile)
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("REDIS_URL", "redis://data@redis:6379/0")
    get_settings.cache_clear()
    client = _seed_app()
    seed_token = sign_service_token(service, SECRET)
    assert client.get("/internal/seed", headers={"X-Service-Token": seed_token}).status_code == 401
    gateway_token = sign_service_token("gateway", SECRET)
    assert (
        client.get("/internal/seed", headers={"X-Service-Token": gateway_token}).status_code == 200
    )


def test_only_the_named_caller_passes_a_single_service_allow_list() -> None:
    """T-189: ``n8n_socioeco`` is accepted by its own route and a gateway token is not."""
    app = FastAPI()

    @app.post("/refresh", dependencies=[Depends(require_service_token("n8n_socioeco"))])
    async def refresh() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    own = {"X-Service-Token": sign_service_token("n8n_socioeco", SECRET)}
    other = {"X-Service-Token": sign_service_token("gateway", SECRET)}
    assert client.post("/refresh", headers=own).status_code == 200
    assert client.post("/refresh", headers=other).status_code == 401
