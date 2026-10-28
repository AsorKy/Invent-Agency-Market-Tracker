"""Unit tests for gateway JWT/JWKS verification, tenant headers, and readiness (REQ-GW-003).

Identity and core are replaced by an in-process fake behind ``httpx.MockTransport``;
tokens are signed with a throwaway RSA key. No network, Redis, or Postgres is used.
This module also hosts the helpers reused by the other gateway test modules.
"""

import base64
import json
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
pytest.importorskip("structlog")
jwt = pytest.importorskip("jwt")
rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")

from libs.common.rate_limiter import RateLimitDecision  # noqa: E402
from services.gateway.app import main as gw  # noqa: E402

REAL_ASYNC_CLIENT = httpx.AsyncClient
TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
LEAD_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Signer:
    """A throwaway RS256 key pair that mints identity-style tokens."""

    def __init__(self, kid: str = "k1") -> None:
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk(self) -> dict[str, str]:
        numbers = self.key.public_key().public_numbers()
        return {
            "kty": "RSA",
            "kid": self.kid,
            "use": "sig",
            "alg": "RS256",
            "n": _b64(numbers.n),
            "e": _b64(numbers.e),
        }

    def token(
        self,
        *,
        tenant: str | None = TENANT_A,
        role: str = "analyst",
        user: str = "user-1",
        scopes: list[dict[str, str]] | None = None,
        ttl: int = 300,
        audience: str = "imt-api",
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": "imt-identity",
            "aud": audience,
            "iat": now,
            "exp": now + ttl,
            "sub": user,
            "role": role,
            "scopes": scopes or [],
        }
        if tenant:
            claims["tenant_id"] = tenant
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


class World:
    """Fake identity and core upstreams that record every request."""

    def __init__(self, signer: Signer) -> None:
        self.signer = signer
        self.requests: list[Any] = []
        self.jwks_down = False
        self.sessions: dict[str, str] = {}  # session cookie value -> access token
        self.leads = {LEAD_A: TENANT_A}

    def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        path = request.url.path
        if path == "/.well-known/jwks.json":
            if self.jwks_down:
                raise httpx.ConnectError("identity down", request=request)
            return httpx.Response(200, json={"keys": [self.signer.jwk()]})
        if path == "/internal/identity/auth/validate":
            cookie = request.headers.get("cookie", "")
            raw = cookie.split("=", 1)[1] if "=" in cookie else ""
            if raw in self.sessions:
                return httpx.Response(200, json={"access_token": self.sessions[raw]})
            return httpx.Response(401, json={"detail": "authentication_required"})
        if path == "/internal/identity/auth/session":
            return httpx.Response(
                200,
                json={"ok": True},
                headers=[
                    ("set-cookie", "session=abc; HttpOnly; Path=/"),
                    ("set-cookie", "csrf=xyz; Path=/"),
                ],
            )
        if path.startswith("/internal/core/leads/"):
            lead = path.rsplit("/", 1)[1]
            if self.leads.get(lead) != request.headers.get("x-tenant-id"):
                return httpx.Response(404, json={"detail": "lead_not_found"})
        return httpx.Response(200, json={"ok": True})

    def core_requests(self) -> list[Any]:
        return [r for r in self.requests if r.url.path.startswith("/internal/core/")]


class StubLimiter:
    async def check(self, identity: str) -> Any:
        return RateLimitDecision(allowed=True, limit=5, remaining=4)


def install(monkeypatch: pytest.MonkeyPatch, handler: Callable[[Any], Any]) -> list[dict[str, Any]]:
    """Route the gateway's httpx client to ``handler``; return the client kwargs used."""
    kwargs_seen: list[dict[str, Any]] = []

    def factory(*args: Any, **kwargs: Any) -> Any:
        kwargs_seen.append(dict(kwargs))
        return REAL_ASYNC_CLIENT(*args, transport=httpx.MockTransport(handler), **kwargs)

    shim = SimpleNamespace(
        AsyncClient=factory,
        HTTPError=httpx.HTTPError,
        TransportError=httpx.TransportError,
        Response=httpx.Response,
    )
    monkeypatch.setattr(gw, "httpx", shim)
    return kwargs_seen


def configure(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True, **settings: Any) -> None:
    """Enable JWT validation, reset caches, and use fast retries."""
    import dataclasses

    base = {
        "upstream_retry_base_delay_seconds": 0,
        "upstream_retry_max_delay_seconds": 0,
        "upstream_retry_attempts": 1,
        **settings,
    }
    monkeypatch.setattr(gw, "settings", gw.settings.model_copy(update=base))
    monkeypatch.setattr(
        gw,
        "security_config",
        dataclasses.replace(gw.security_config, identity_validation_enabled=enabled),
    )
    monkeypatch.setattr(
        gw,
        "jwks_cache",
        gw.JwksCache(
            lambda: gw.settings.identity_url.rstrip("/") + "/.well-known/jwks.json",
            lambda: gw._client(5.0),
            gw._http_errors,
        ),
    )
    monkeypatch.setattr(gw.readiness, "state", {})


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    fake = World(Signer())
    install(monkeypatch, fake)
    configure(monkeypatch)
    return fake


@pytest.fixture
async def client(asgi_client: Any, world: World) -> Any:
    async with asgi_client(gw.app) as http:
        gw.app.state.rate_limiter = StubLimiter()
        yield http


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- verification ------------------------------------------------------------


async def test_valid_bearer_token_sets_tenant_and_role_headers(client: Any, world: World) -> None:
    token = world.signer.token(role="admin", user="u-9")

    response = await client.get("/api/v1/companies", headers=bearer(token))

    assert response.status_code == 200
    sent = world.core_requests()[0]
    assert sent.headers["x-tenant-id"] == TENANT_A
    assert sent.headers["x-user-role"] == "admin"
    assert sent.headers["x-user-id"] == "u-9"
    assert sent.headers["authorization"] == f"Bearer {token}"


async def test_missing_or_garbage_credentials_are_401(client: Any, world: World) -> None:
    assert (await client.get("/api/v1/companies")).status_code == 401
    assert (await client.get("/api/v1/companies", headers=bearer("not.a.jwt"))).status_code == 401
    assert world.core_requests() == []


async def test_expired_wrong_audience_and_foreign_key_tokens_are_401(
    client: Any, world: World
) -> None:
    stranger = Signer(kid="k1")  # same kid, different key
    tokens = [
        world.signer.token(ttl=-60),
        world.signer.token(audience="someone-else"),
        stranger.token(),
    ]
    for token in tokens:
        assert (await client.get("/api/v1/companies", headers=bearer(token))).status_code == 401
    assert world.core_requests() == []


async def test_service_and_tenantless_tokens_are_rejected_on_public_api(
    client: Any, world: World
) -> None:
    for token in (world.signer.token(role="service"), world.signer.token(tenant=None)):
        assert (await client.get("/api/v1/companies", headers=bearer(token))).status_code == 401


async def test_unsigned_alg_none_token_is_rejected(client: Any, world: World) -> None:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "kid": "k1"}).encode()).strip(b"=")
    body = base64.urlsafe_b64encode(
        json.dumps({"sub": "x", "tenant_id": TENANT_A, "role": "admin"}).encode()
    ).strip(b"=")
    forged = f"{header.decode()}.{body.decode()}."

    assert (await client.get("/api/v1/companies", headers=bearer(forged))).status_code == 401


async def test_client_supplied_tenant_and_role_headers_are_never_forwarded(
    client: Any, world: World
) -> None:
    forged = {"X-Tenant-Id": TENANT_B, "X-User-Role": "admin", "X-User-Id": "evil"}

    await client.get(
        "/api/v1/companies", headers={**forged, **bearer(world.signer.token(role="analyst"))}
    )

    sent = world.core_requests()[0]
    assert sent.headers["x-tenant-id"] == TENANT_A
    assert sent.headers["x-user-role"] == "analyst"
    assert sent.headers["x-user-id"] == "user-1"


async def test_forged_tenant_header_is_dropped_even_when_validation_is_off(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, enabled=False)

    await client.get("/api/v1/companies", headers={"X-Tenant-Id": TENANT_B, "X-User-Role": "admin"})

    sent = world.core_requests()[0]
    assert "x-tenant-id" not in sent.headers and "x-user-role" not in sent.headers


# --- JWKS cache --------------------------------------------------------------


async def test_jwks_is_cached_for_ten_minutes(client: Any, world: World) -> None:
    clock = {"now": 1000.0}
    gw.jwks_cache._clock = lambda: clock["now"]
    headers = bearer(world.signer.token())

    await client.get("/api/v1/companies", headers=headers)
    await client.get("/api/v1/companies", headers=headers)
    assert gw.jwks_cache.fetch_count == 1

    clock["now"] += 601
    await client.get("/api/v1/companies", headers=headers)
    assert gw.jwks_cache.fetch_count == 2


async def test_unknown_kid_triggers_one_refresh_then_verifies(client: Any, world: World) -> None:
    clock = {"now": 1000.0}
    gw.jwks_cache._clock = lambda: clock["now"]
    await client.get("/api/v1/companies", headers=bearer(world.signer.token()))
    assert gw.jwks_cache.fetch_count == 1

    clock["now"] += 60  # key rotation at identity
    world.signer = Signer(kid="k2")
    response = await client.get("/api/v1/companies", headers=bearer(world.signer.token()))

    assert response.status_code == 200
    assert gw.jwks_cache.fetch_count == 2


async def test_stale_jwks_keeps_working_when_identity_is_down(client: Any, world: World) -> None:
    clock = {"now": 1000.0}
    gw.jwks_cache._clock = lambda: clock["now"]
    headers = bearer(world.signer.token())
    await client.get("/api/v1/companies", headers=headers)

    world.jwks_down = True
    clock["now"] += 700

    assert (await client.get("/api/v1/companies", headers=headers)).status_code == 200


async def test_jwks_unreachable_on_cold_cache_is_503(client: Any, world: World) -> None:
    world.jwks_down = True

    response = await client.get("/api/v1/companies", headers=bearer(world.signer.token()))

    assert response.status_code == 503


# --- session cookie flow -----------------------------------------------------


async def test_session_cookie_is_exchanged_for_a_jwt_at_identity(client: Any, world: World) -> None:
    world.sessions["abc"] = world.signer.token(role="strategist")

    response = await client.get("/api/v1/companies", headers={"Cookie": "session=abc"})

    assert response.status_code == 200
    validate = [r for r in world.requests if r.url.path.endswith("/auth/validate")]
    assert len(validate) == 1 and validate[0].headers["cookie"] == "session=abc"
    assert world.core_requests()[0].headers["x-user-role"] == "strategist"
    assert "cookie" not in world.core_requests()[0].headers


async def test_unknown_session_cookie_is_401(client: Any, world: World) -> None:
    response = await client.get("/api/v1/companies", headers={"Cookie": "session=nope"})

    assert response.status_code == 401


async def test_login_needs_no_jwt_and_passes_every_set_cookie(client: Any, world: World) -> None:
    response = await client.post("/api/v1/auth/session", content=b"{}")

    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2 and cookies[0].startswith("session=abc")


async def test_identity_internal_endpoints_are_not_public(client: Any, world: World) -> None:
    headers = bearer(world.signer.token(role="admin"))
    for path in (
        "/api/v1/auth/validate",
        "/api/v1/auth/service-token",
        "/api/v1/tokens/user",
        "/api/v1/internal/identity/me",
        "/admin/login/",
        "/api/v1/admin/",
    ):
        response = await client.get(path, headers=headers)
        assert response.status_code == 404, path
    assert world.requests == [] or all(r.url.path.endswith("jwks.json") for r in world.requests)


# --- readiness and timeouts --------------------------------------------------


async def test_only_routes_of_a_red_upstream_return_503(client: Any, world: World) -> None:
    headers = bearer(world.signer.token())
    gw.readiness.state.update({"brief": False, "core": True})

    brief = await client.get(f"/api/v1/leads/{LEAD_A}/brief", headers=headers)
    core = await client.get("/api/v1/companies", headers=headers)

    assert brief.status_code == 503
    assert core.status_code == 200
    assert not any("/internal/brief/" in r.url.path for r in world.requests)


async def test_readiness_poller_marks_services_from_ready_endpoints(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    ml_port = httpx.URL(gw.settings.ml_url).port

    def handler(request: Any) -> Any:
        status = 503 if request.url.port == ml_port else 200
        return httpx.Response(status)

    install(monkeypatch, handler)

    await gw.readiness.refresh()

    assert gw.readiness.is_red("ml") and not gw.readiness.is_red("core")
    assert gw.readiness.interval == 10
    assert "analytics" not in gw.readiness.state


async def test_per_route_upstream_timeouts(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = install(monkeypatch, world)
    headers = bearer(world.signer.token(role="admin"))

    await client.post("/api/v1/imports/companies", content=b"{}", headers=headers)
    await client.get("/api/v1/companies", headers=headers)

    timeouts = [k["timeout"] for k in seen if k.get("timeout") is not None]
    assert 60.0 in timeouts and 10.0 in timeouts


async def test_admin_tenant_config_requires_admin_role(client: Any, world: World) -> None:
    analyst = bearer(world.signer.token(role="analyst"))
    admin = bearer(world.signer.token(role="admin"))

    assert (await client.get("/api/v1/admin/tenant-config", headers=analyst)).status_code == 403
    assert (await client.get("/api/v1/admin/tenant-config", headers=admin)).status_code == 200
    put = await client.put("/api/v1/admin/tenant-config", content=b"{}", headers=admin)
    assert put.status_code == 200
    assert (await client.post("/api/v1/admin/tenant-config", headers=admin)).status_code == 405


async def test_opt_out_is_admin_only_and_tenant_comes_from_the_jwt(
    client: Any, world: World
) -> None:
    body = b'{"nit": "900123456", "tenant_id": "%s"}' % TENANT_B.encode()
    for role in ("strategist", "analyst"):
        denied = await client.post(
            "/api/v1/compliance/opt-out",
            content=body,
            headers=bearer(world.signer.token(role=role)),
        )
        assert denied.status_code == 403
    assert world.core_requests() == []

    admin = bearer(world.signer.token(role="admin"))
    allowed = await client.post(
        "/api/v1/compliance/opt-out", content=body, headers={**admin, "X-Tenant-Id": TENANT_B}
    )

    assert allowed.status_code == 200
    sent = world.core_requests()[0]
    assert sent.url.path == "/internal/core/compliance/opt-out"
    assert sent.headers["x-tenant-id"] == TENANT_A
    assert (await client.get("/api/v1/compliance/opt-out", headers=admin)).status_code == 405
    assert (await client.post("/api/v1/compliance/purge", headers=admin)).status_code == 404
