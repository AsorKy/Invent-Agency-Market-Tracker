"""Unit tests for the local and production profiles at the gateway (REQ-API-003).

Local: CORS allowlist with credentials for ``FRONTEND_ORIGIN`` and cookie names
without the ``__Host-`` prefix. Production: no CORS and prefixed cookie names.
The identity cookie attributes of both profiles are covered in
``test_identity_session.py``.
"""

import dataclasses
from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("structlog")
pytest.importorskip("jwt")
pytest.importorskip("cryptography")

import test_gateway_auth as base  # noqa: E402
from test_gateway_auth import World, bearer, configure  # noqa: E402

client = base.client
world = base.world

from services.gateway.app import main as gw  # noqa: E402

ORIGIN = "http://localhost:3000"


def _profile(monkeypatch: pytest.MonkeyPatch, profile: str, **extra: Any) -> None:
    configure(monkeypatch, app_profile=profile, frontend_origin=ORIGIN, **extra)


async def test_local_preflight_allows_frontend_origin_with_credentials(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile(monkeypatch, "local")

    response = await client.options(
        "/api/v1/leads",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-csrf-token",
        },
    )

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "X-CSRF-Token" in response.headers["access-control-allow-headers"]
    assert world.requests == []


async def test_local_cors_headers_on_actual_responses(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile(monkeypatch, "local")

    allowed = await client.get(
        "/api/v1/companies", headers={**bearer(world.signer.token()), "Origin": ORIGIN}
    )
    other = await client.get(
        "/api/v1/companies",
        headers={**bearer(world.signer.token()), "Origin": "http://evil.example"},
    )

    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in other.headers


async def test_production_has_no_cors(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile(monkeypatch, "production")

    preflight = await client.options(
        "/api/v1/leads",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    actual = await client.get(
        "/api/v1/companies", headers={**bearer(world.signer.token()), "Origin": ORIGIN}
    )

    assert "access-control-allow-origin" not in preflight.headers
    assert "access-control-allow-origin" not in actual.headers


def test_cookie_names_follow_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    _profile(monkeypatch, "local")
    assert (gw.session_cookie_name(), gw.csrf_cookie_name()) == ("session", "csrf")
    _profile(monkeypatch, "production")
    assert (gw.session_cookie_name(), gw.csrf_cookie_name()) == ("__Host-session", "__Host-csrf")


@pytest.mark.parametrize(
    ("profile", "good", "bad"),
    [("local", "csrf", "__Host-csrf"), ("production", "__Host-csrf", "csrf")],
)
async def test_csrf_cookie_name_is_enforced_per_profile(
    client: Any,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    good: str,
    bad: str,
) -> None:
    _profile(monkeypatch, profile)
    monkeypatch.setattr(
        gw, "security_config", dataclasses.replace(gw.security_config, csrf_enabled=True)
    )
    auth = bearer(world.signer.token())

    ok = await client.post(
        "/api/v1/projects",
        content=b"{}",
        headers={**auth, "Cookie": f"{good}=t1", "X-CSRF-Token": "t1"},
    )
    wrong = await client.post(
        "/api/v1/projects",
        content=b"{}",
        headers={**auth, "Cookie": f"{bad}=t1", "X-CSRF-Token": "t1"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 403 and wrong.json() == {"detail": "csrf_token_invalid"}


async def test_session_cookie_name_follows_the_profile(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.sessions["abc"] = world.signer.token()
    _profile(monkeypatch, "production")

    prefixed = await client.get("/api/v1/companies", headers={"Cookie": "__Host-session=abc"})
    plain = await client.get("/api/v1/companies", headers={"Cookie": "session=abc"})

    assert prefixed.status_code == 200
    assert plain.status_code == 401
