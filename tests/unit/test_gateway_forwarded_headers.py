"""The gateway forwards an allow list of client request headers (REQ-API-002, REQ-SEC-005).

Core needs ``Idempotency-Key`` on lead creation and CSV imports, ``X-Filename`` and the raw CSV
``Content-Type`` on imports, and ``If-Match`` on optimistic updates; query parameters such as
``preserve_blob`` pass through unchanged. Identity headers are never taken from the client:
cookies, the CSRF token and the edge secret stay at the gateway, and ``Authorization`` and the
tenant, user, role, scope and service-token headers are set by the gateway alone. Reuses the
fake upstreams of ``test_gateway_auth``; no network, Redis, or Postgres is used.
"""

from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("structlog")
pytest.importorskip("jwt")
pytest.importorskip("cryptography")

import test_gateway_auth as base  # noqa: E402
from test_gateway_auth import TENANT_A, TENANT_B, World, bearer, configure  # noqa: E402

client = base.client
world = base.world

SPOOFED = {
    "X-Tenant-Id": TENANT_B,
    "X-User-Id": "intruder",
    "X-User-Role": "admin",
    "X-User-Scopes": "[]",
    "X-Service-Token": "v1.gateway.9999999999.forged",
    "X-Edge-Secret": "guessed",
    "X-CSRF-Token": "csrf-from-browser",
    "X-Forwarded-For": "203.0.113.9",
    "Cookie": "session=browser-session; csrf=csrf-from-browser",
}
NEVER_FROM_CLIENT = ("cookie", "x-edge-secret", "x-csrf-token", "x-forwarded-for")


async def test_lead_creation_forwards_the_idempotency_key(client: Any, world: World) -> None:
    headers = {**bearer(world.signer.token(role="strategist")), "Idempotency-Key": "lead-1"}

    await client.post("/api/v1/leads", json={"company_id": "x"}, headers=headers)

    sent = world.core_requests()[0]
    assert sent.url.path == "/internal/core/leads"
    assert sent.headers["idempotency-key"] == "lead-1"
    assert sent.headers["content-type"] == "application/json"


async def test_csv_import_forwards_its_headers_query_and_raw_body(
    client: Any, world: World
) -> None:
    csv = b"legal_name,city\nAcme,Bogota\n"
    headers = {
        **bearer(world.signer.token(role="admin")),
        "Idempotency-Key": "import-7",
        "X-Filename": "companies.csv",
        "Content-Type": "text/csv",
    }

    await client.post(
        "/api/v1/imports/companies",
        params={"preserve_blob": "true"},
        content=csv,
        headers=headers,
    )

    sent = world.core_requests()[0]
    assert sent.url.path == "/internal/core/imports/companies"
    assert dict(sent.url.params) == {"preserve_blob": "true"}
    assert sent.headers["idempotency-key"] == "import-7"
    assert sent.headers["x-filename"] == "companies.csv"
    assert sent.headers["content-type"] == "text/csv"
    assert sent.content == csv


async def test_optimistic_update_forwards_if_match(client: Any, world: World) -> None:
    headers = {**bearer(world.signer.token(role="admin")), "If-Match": '"3"'}

    await client.put("/api/v1/admin/tenant-config", json={}, headers=headers)

    assert world.core_requests()[0].headers["if-match"] == '"3"'


async def test_spoofed_identity_headers_are_dropped_with_a_token(client: Any, world: World) -> None:
    token = world.signer.token(role="analyst")

    await client.get("/api/v1/companies", headers={**SPOOFED, **bearer(token)})

    sent = world.core_requests()[0]
    assert not [name for name in NEVER_FROM_CLIENT if name in sent.headers]
    assert sent.headers["x-tenant-id"] == TENANT_A
    assert sent.headers["x-user-id"] == "user-1"
    assert sent.headers["x-user-role"] == "analyst"
    assert sent.headers["x-service-token"] != SPOOFED["X-Service-Token"]
    assert sent.headers["x-service-token"].startswith("v1.gateway.")
    # The gateway sets Authorization itself from the verified token, never from a client header.
    assert sent.headers["authorization"] == f"Bearer {token}"


async def test_spoofed_identity_headers_are_dropped_with_validation_off(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, enabled=False)

    await client.post(
        "/api/v1/leads", json={}, headers={**SPOOFED, "Authorization": "Bearer browser"}
    )

    sent = world.core_requests()[0]
    dropped = (
        *NEVER_FROM_CLIENT,
        "authorization",
        "x-tenant-id",
        "x-user-id",
        "x-user-role",
        "x-user-scopes",
    )
    assert not [name for name in dropped if name in sent.headers]
    assert sent.headers["x-service-token"] != SPOOFED["X-Service-Token"]


async def test_identity_still_gets_its_cookie_and_csrf_token(client: Any, world: World) -> None:
    await client.post(
        "/api/v1/auth/session",
        content=b"{}",
        headers={"Cookie": "session=abc", "X-CSRF-Token": "t", "X-Tenant-Id": TENANT_B},
    )

    (sent,) = [r for r in world.requests if r.url.path == "/internal/identity/auth/session"]
    assert sent.headers["cookie"] == "session=abc" and sent.headers["x-csrf-token"] == "t"
    assert "x-tenant-id" not in sent.headers
