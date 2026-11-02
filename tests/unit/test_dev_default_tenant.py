"""Unit tests for the local-only ``DEV_DEFAULT_TENANT_ID`` gateway fallback.

With identity validation off the gateway forwards no tenant, so row level security
hides every row. The setting supplies ``X-Tenant-Id`` (never a role) on routes that
need no role and on GET routes restricted to the read roles (admin, strategist,
analyst: the data explorer, funnel and validation cards), never on admin-only,
identity, or role-restricted write routes; a verified claim wins, validation on
ignores it, and the settings refuse it in production with the D-28 production
rule. Reuses the fake upstreams of ``test_gateway_auth``; no network, Redis, or
Postgres is used.
"""

import logging
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("structlog")
pytest.importorskip("jwt")
pytest.importorskip("cryptography")

import test_gateway_auth as base  # noqa: E402
from test_gateway_auth import TENANT_A, TENANT_B, World, bearer, configure  # noqa: E402

from libs.common.data_explorer import DATA_SERVICES  # noqa: E402
from libs.common.settings import Settings  # noqa: E402
from services.gateway.app import main as gw  # noqa: E402

client = base.client
world = base.world

DEMO_TENANT = "00000000-0000-4000-8000-0000000000a1"
PROJECT = "33333333-3333-4333-8333-333333333333"
COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
ROLE_HEADERS = ("x-user-role", "x-user-id", "x-user-scopes", "authorization")


def dev(monkeypatch: pytest.MonkeyPatch, *, enabled: bool, tenant: str | None) -> None:
    configure(monkeypatch, enabled=enabled, dev_default_tenant_id=UUID(tenant) if tenant else None)


# --- gateway forwarding ------------------------------------------------------


async def test_validation_off_forwards_the_dev_tenant_and_no_role(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev(monkeypatch, enabled=False, tenant=DEMO_TENANT)

    response = await client.get("/api/v1/companies", headers={"X-Tenant-Id": TENANT_B})

    assert response.status_code == 200
    sent = world.core_requests()[0]
    assert sent.headers["x-tenant-id"] == DEMO_TENANT
    assert not [name for name in ROLE_HEADERS if name in sent.headers]


async def test_admin_only_routes_and_role_restricted_writes_never_get_the_dev_tenant(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev(monkeypatch, enabled=False, tenant=DEMO_TENANT)

    await client.get("/api/v1/admin/tenant-config")
    await client.put("/api/v1/admin/tenant-config", content=b"{}")
    await client.post("/api/v1/compliance/opt-out", content=b"{}")
    await client.post(f"/api/v1/funnel/projects/{PROJECT}/gates/g1/decision", content=b"{}")
    await client.put(f"/api/v1/projects/{PROJECT}/validation-inputs", content=b"{}")

    sent = world.core_requests()
    assert len(sent) == 5
    assert not [r.url.path for r in sent if "x-tenant-id" in r.headers]


async def test_read_role_get_routes_get_the_dev_tenant_and_no_role(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev(monkeypatch, enabled=False, tenant=DEMO_TENANT)

    await client.get("/api/v1/validation/meta-ml", headers={"X-Tenant-Id": TENANT_B})
    await client.get(f"/api/v1/funnel/projects/{PROJECT}")
    await client.get("/api/v1/data/companies")
    await client.get("/api/v1/data/raw_snapshots")
    catalog = await client.get("/api/v1/data/catalog")

    assert catalog.status_code == 200
    sent = [r for r in world.requests if r.url.path.startswith("/internal/")]
    assert {r.url.path for r in sent} == {
        "/internal/core/validation/meta-ml",
        f"/internal/core/funnel/projects/{PROJECT}",
        "/internal/core/data/companies",
        "/internal/ingest/data/raw_snapshots",
        *(f"/internal/{service}/data/catalog" for service in DATA_SERVICES),
    }
    assert {r.headers["x-tenant-id"] for r in sent} == {DEMO_TENANT}
    assert not [name for r in sent for name in ROLE_HEADERS if name in r.headers]


async def test_validation_off_without_the_dev_tenant_sends_no_tenant_to_the_data_explorer(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev(monkeypatch, enabled=False, tenant=None)

    await client.get("/api/v1/data/companies", headers={"X-Tenant-Id": TENANT_B})

    assert "x-tenant-id" not in world.core_requests()[0].headers


async def test_identity_routes_never_get_the_dev_tenant(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev(monkeypatch, enabled=False, tenant=DEMO_TENANT)

    await client.post("/api/v1/auth/session", content=b"{}")

    sent = [r for r in world.requests if r.url.path == "/internal/identity/auth/session"]
    assert len(sent) == 1 and "x-tenant-id" not in sent[0].headers


async def test_verified_claim_wins_over_the_dev_tenant(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev(monkeypatch, enabled=True, tenant=DEMO_TENANT)

    await client.get("/api/v1/companies", headers=bearer(world.signer.token(role="analyst")))

    sent = world.core_requests()[0]
    assert sent.headers["x-tenant-id"] == TENANT_A
    assert sent.headers["x-user-role"] == "analyst"


async def test_validation_on_ignores_the_dev_tenant(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev(monkeypatch, enabled=True, tenant=DEMO_TENANT)

    assert (await client.get("/api/v1/companies")).status_code == 401
    await client.post("/api/v1/auth/session", content=b"{}")

    assert world.core_requests() == []
    assert not [r for r in world.requests if "x-tenant-id" in r.headers]
    assert gw.dev_tenant_id() is None


async def test_unset_dev_tenant_leaves_validation_off_unchanged(
    client: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev(monkeypatch, enabled=False, tenant=None)

    response = await client.get("/api/v1/companies", headers={"X-Tenant-Id": TENANT_B})

    assert response.status_code == 200
    assert "x-tenant-id" not in world.core_requests()[0].headers


# --- startup warning ---------------------------------------------------------


@pytest.mark.parametrize(
    ("enabled", "tenant", "warned"),
    [(False, DEMO_TENANT, True), (True, DEMO_TENANT, False), (False, None, False)],
)
async def test_startup_warns_once_only_while_the_dev_tenant_is_active(
    asgi_client: Any,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    enabled: bool,
    tenant: str | None,
    warned: bool,
) -> None:
    dev(monkeypatch, enabled=enabled, tenant=tenant)

    with caplog.at_level(logging.WARNING):
        async with asgi_client(gw.app):
            pass

    messages = [record.getMessage() for record in caplog.records]
    lines = [message for message in messages if "dev_default_tenant_active" in message]
    assert len(lines) == (1 if warned else 0)
    if warned:
        assert "DEV_DEFAULT_TENANT_ID" in lines[0] and DEMO_TENANT not in lines[0]


# --- settings ----------------------------------------------------------------


@pytest.fixture
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_PROFILE", "ENVIRONMENT", "REDIS_URL", "DEV_DEFAULT_TENANT_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("_clean_env")
@pytest.mark.parametrize(
    ("profile", "environment"),
    [("production", "local"), ("local", "production"), ("production", "production")],
)
def test_settings_refuse_the_dev_tenant_in_production(profile: str, environment: str) -> None:
    with pytest.raises(ValueError, match="DEV_DEFAULT_TENANT_ID is refused in production"):
        Settings(
            app_profile=profile,
            environment=environment,
            redis_url="redis://data@redis:6379/0",
            dev_default_tenant_id=DEMO_TENANT,
            _env_file=None,  # type: ignore[call-arg]
        )


@pytest.mark.usefixtures("_clean_env")
def test_settings_accept_production_without_the_dev_tenant() -> None:
    settings = Settings(
        app_profile="production",
        environment="production",
        redis_url="redis://data@redis:6379/0",
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.dev_default_tenant_id is None


@pytest.mark.usefixtures("_clean_env")
@pytest.mark.parametrize("environment", ["local", "ci"])
def test_settings_accept_the_dev_tenant_locally(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("DEV_DEFAULT_TENANT_ID", DEMO_TENANT)
    assert Settings(_env_file=None).dev_default_tenant_id == UUID(DEMO_TENANT)  # type: ignore[call-arg]


@pytest.mark.usefixtures("_clean_env")
def test_blank_dev_tenant_is_unset_and_garbage_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_DEFAULT_TENANT_ID", "")
    assert Settings(_env_file=None).dev_default_tenant_id is None  # type: ignore[call-arg]
    monkeypatch.setenv("DEV_DEFAULT_TENANT_ID", "not-a-uuid")
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


# --- wiring ------------------------------------------------------------------


def test_compose_passes_the_dev_tenant_to_the_gateway_only() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    holders = {
        name: str(svc["environment"]["DEV_DEFAULT_TENANT_ID"])
        for name, svc in services.items()
        if "DEV_DEFAULT_TENANT_ID" in (svc.get("environment") or {})
    }
    assert holders == {"gateway": "${DEV_DEFAULT_TENANT_ID:-}"}
