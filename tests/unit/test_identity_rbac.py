"""Unit tests for the identity service: RBAC, JWKS, service tokens, admin guard.

Uses ``services.identity.project.settings_test`` (SQLite) and the Django test client. Relates to
services/identity (REQ-IDN-001).
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import os
from collections.abc import Iterator

import jwt
import pytest

os.environ["DJANGO_SETTINGS_MODULE"] = "services.identity.project.settings_test"

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from django.test import Client  # noqa: E402

from services.identity.iam import rbac  # noqa: E402
from services.identity.iam.models import (  # noqa: E402
    MembershipScope,
    Tenant,
    TenantMembership,
    UserAccount,
)
from tests.unit.service_auth import service_headers  # noqa: E402

MODELS = (Tenant, UserAccount, TenantMembership, MembershipScope)
TOKEN = {"HTTP_X_SERVICE_TOKEN": service_headers("gateway")["X-Service-Token"]}


@pytest.fixture
def db() -> Iterator[None]:
    with connection.schema_editor() as editor:
        for model in MODELS:
            editor.create_model(model)
    yield
    with connection.schema_editor() as editor:
        for model in reversed(MODELS):
            editor.delete_model(model)


def _membership(role: str = "bd_rep") -> TenantMembership:
    tenant = Tenant.objects.create(slug="acme", display_name="Acme")
    user = UserAccount.objects.create(email="a@example.com")
    MembershipScope.objects.create(
        tenant_id=tenant.id, user_id=user.id, city="Bogota", service_line="events"
    )
    return TenantMembership.objects.create(tenant=tenant, user=user, role=role)


def _verify(token: str, client: Client) -> dict[str, object]:
    keys = client.get("/.well-known/jwks.json").json()["keys"]
    kid = jwt.get_unverified_header(token)["kid"]
    jwk = next(k for k in keys if k["kid"] == kid)
    public = jwt.PyJWK(jwk).key
    return dict(jwt.decode(token, public, algorithms=["RS256"], audience="imt-api"))


def test_role_matrix_matches_adr() -> None:
    assert set(rbac.ROLES) == {"admin", "strategist", "intake", "bd_rep", "analyst"}
    assert rbac.has_permission("admin", "user.manage")
    assert not rbac.has_permission("strategist", "lead.edit")
    assert rbac.has_permission("strategist", "lead.approve")
    assert not rbac.has_permission("intake", "pulse.view")
    assert not rbac.has_permission("analyst", "lead.edit")
    assert rbac.has_permission("analyst", "report.export")
    assert rbac.permissions_for("unknown") == frozenset()


def test_scope_filtering() -> None:
    scopes = [("Bogota", "events")]
    assert rbac.scope_allows("bd_rep", scopes, "Bogota", "events")
    assert not rbac.scope_allows("bd_rep", scopes, "Medellin", "events")
    assert not rbac.scope_allows("intake", [], "Bogota", "events")
    assert rbac.scope_allows("intake", [("", "events")], "Cali", "events")
    assert rbac.scope_allows("analyst", [], "Cali", "events")
    assert not rbac.scope_allows("ghost", [], "Cali", "events")


def test_jwks_publishes_rs256_public_key_only() -> None:
    body = Client().get("/.well-known/jwks.json").json()
    assert len(body["keys"]) == 1
    key = body["keys"][0]
    assert key["alg"] == "RS256" and key["kty"] == "RSA" and key["kid"]
    assert "d" not in key and "p" not in key


def test_service_token_requires_internal_token_and_verifies() -> None:
    client = Client()
    assert client.post("/internal/identity/service-token", {"service": "core"}).status_code == 403
    bad = client.post(
        "/internal/identity/service-token", {"service": "core"}, HTTP_X_SERVICE_TOKEN="nope"
    )
    assert bad.status_code == 403
    invalid = client.post("/internal/identity/service-token", {"service": "x y"}, **TOKEN)
    assert invalid.status_code == 400
    ok = client.post("/internal/identity/service-token", {"service": "core"}, **TOKEN)
    assert ok.status_code == 200
    claims = _verify(ok.json()["access_token"], client)
    assert claims["sub"] == "service:core" and claims["role"] == "service"


def test_user_token_carries_role_tenant_and_scopes(db: None) -> None:
    m = _membership("bd_rep")
    client = Client()
    resp = client.post(
        "/internal/identity/tokens/user",
        {"user_id": str(m.user_id), "tenant_id": str(m.tenant_id)},
        **TOKEN,
    )
    assert resp.status_code == 200
    claims = _verify(resp.json()["access_token"], client)
    assert claims["role"] == "bd_rep"
    assert claims["tenant_id"] == str(m.tenant_id)
    assert claims["scopes"] == [{"city": "Bogota", "service_line": "events"}]
    assert "lead.contact" in claims["permissions"]  # type: ignore[operator]


def test_user_token_rejects_inactive_and_unknown(db: None) -> None:
    m = _membership("analyst")
    client = Client()
    payload = {"user_id": str(m.user_id), "tenant_id": str(m.tenant_id)}
    m.user.status = "disabled"
    m.user.save()
    assert client.post("/internal/identity/tokens/user", payload, **TOKEN).status_code == 403
    missing = {"user_id": "not-a-uuid", "tenant_id": str(m.tenant_id)}
    assert client.post("/internal/identity/tokens/user", missing, **TOKEN).status_code == 404


def test_admin_only_on_internal_network() -> None:
    assert Client(REMOTE_ADDR="10.1.2.3").get("/admin/login/").status_code == 200
    assert Client(REMOTE_ADDR="8.8.8.8").get("/admin/login/").status_code == 404
    proxied = Client(REMOTE_ADDR="10.1.2.3", HTTP_X_FORWARDED_FOR="8.8.8.8")
    assert proxied.get("/admin/login/").status_code == 404
