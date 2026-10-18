"""Unit tests for tenant isolation and scoped roles at the gateway (REQ-IDN-003).

A fake core answers 404 when the ``X-Tenant-Id`` header the gateway sets does not
own the lead, which mirrors RLS. Tests prove the header can only come from the
verified token and that ``intake`` and ``bd_rep`` stay inside their scopes.
"""

import json
from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("structlog")
pytest.importorskip("jwt")
pytest.importorskip("cryptography")

import test_gateway_auth as base  # noqa: E402
from test_gateway_auth import LEAD_A, TENANT_A, TENANT_B, World, bearer  # noqa: E402

client = base.client
world = base.world

BD_SCOPES = [{"city": "Bogota", "service_line": "events"}]


async def test_tenant_b_user_gets_404_for_tenant_a_lead(client: Any, world: World) -> None:
    token_b = world.signer.token(tenant=TENANT_B, role="admin")

    response = await client.get(f"/api/v1/leads/{LEAD_A}", headers=bearer(token_b))

    assert response.status_code == 404
    assert world.core_requests()[0].headers["x-tenant-id"] == TENANT_B


async def test_tenant_a_user_reads_own_lead(client: Any, world: World) -> None:
    response = await client.get(f"/api/v1/leads/{LEAD_A}", headers=bearer(world.signer.token()))

    assert response.status_code == 200


async def test_tenant_header_forgery_cannot_cross_tenants(client: Any, world: World) -> None:
    headers = {**bearer(world.signer.token(tenant=TENANT_B)), "X-Tenant-Id": TENANT_A}

    response = await client.get(f"/api/v1/leads/{LEAD_A}", headers=headers)

    assert response.status_code == 404


async def test_bd_rep_scopes_are_forwarded_to_core(client: Any, world: World) -> None:
    token = world.signer.token(role="bd_rep", scopes=BD_SCOPES)

    await client.get("/api/v1/leads", headers=bearer(token))

    sent = world.core_requests()[0]
    assert json.loads(sent.headers["x-user-scopes"]) == BD_SCOPES
    assert sent.headers["x-user-role"] == "bd_rep"


@pytest.mark.parametrize("role", ["bd_rep", "intake"])
async def test_scoped_roles_cannot_filter_outside_their_scope(
    client: Any, world: World, role: str
) -> None:
    token = world.signer.token(role=role, scopes=BD_SCOPES)

    outside = await client.get("/api/v1/leads?city=Medellin", headers=bearer(token))
    wrong_line = await client.get(
        "/api/v1/leads?city=Bogota&service_line=aaas", headers=bearer(token)
    )
    inside = await client.get(
        "/api/v1/leads?city=Bogota&service_line=events", headers=bearer(token)
    )

    assert outside.status_code == 404 and wrong_line.status_code == 404
    assert inside.status_code == 200
    assert len(world.core_requests()) == 1


async def test_scoped_role_without_any_scope_sees_nothing(client: Any, world: World) -> None:
    token = world.signer.token(role="intake", scopes=[])

    response = await client.get("/api/v1/leads?city=Bogota", headers=bearer(token))

    assert response.status_code == 404


async def test_unscoped_roles_may_filter_any_city(client: Any, world: World) -> None:
    token = world.signer.token(role="analyst")

    response = await client.get("/api/v1/leads?city=Medellin", headers=bearer(token))

    assert response.status_code == 200
