"""Unit tests for the public route table (REQ-API-002).

Checks that each public endpoint reaches the owning service, that L2 and L3
resources answer ``not_in_layer``, that nothing reaches ``/internal`` or the
retired ``analytics`` service, and that no source file still mentions it.
"""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
pytest.importorskip("structlog")
pytest.importorskip("jwt")
pytest.importorskip("cryptography")

import test_gateway_auth as base  # noqa: E402
from test_gateway_auth import World, bearer  # noqa: E402

client = base.client
world = base.world

from libs.common.settings import get_settings  # noqa: E402
from services.gateway.app import main as gw  # noqa: E402
from services.gateway.app.routes import NotInLayer, Route, resolve  # noqa: E402

PORTS = {
    "core": 8002,
    "identity": 8005,
    "pulse": 8006,
    "brief": 8007,
    "ingest": 8001,
    "ml": 8004,
}

TABLE = [
    ("POST", "auth/session", "identity", "/internal/identity/auth/session"),
    ("GET", "me", "identity", "/internal/identity/me"),
    ("POST", "logout", "identity", "/internal/identity/logout"),
    ("GET", "leads", "core", "/internal/core/leads"),
    ("POST", "leads", "core", "/internal/core/leads"),
    ("GET", "leads/7", "core", "/internal/core/leads/7"),
    ("PATCH", "leads/7/status", "core", "/internal/core/leads/7/status"),
    ("POST", "leads/7/activities", "core", "/internal/core/leads/7/activities"),
    ("GET", "leads/7/brief", "brief", "/internal/brief/leads/7"),
    ("GET", "pipeline/kanban", "core", "/internal/core/pipeline/kanban"),
    ("GET", "companies", "core", "/internal/core/companies"),
    ("GET", "signals", "core", "/internal/core/signals"),
    ("GET", "projects", "core", "/internal/core/projects"),
    ("POST", "projects", "core", "/internal/core/projects"),
    ("GET", "pulse/market", "pulse", "/internal/pulse/market"),
    ("GET", "pulse/pipeline-backlog", "pulse", "/internal/pulse/pipeline-backlog"),
    ("GET", "ops/health", "pulse", "/internal/pulse/ops/health"),
    ("GET", "territory/opportunity", "pulse", "/internal/pulse/territory/opportunity"),
    ("POST", "imports/companies", "core", "/internal/core/imports/companies"),
    ("GET", "jobs/5", "core", "/internal/core/jobs/5"),
    ("GET", "admin/tenant-config", "core", "/internal/core/admin/tenant-config"),
    ("PUT", "admin/tenant-config", "core", "/internal/core/admin/tenant-config"),
    ("POST", "compliance/opt-out", "core", "/internal/core/compliance/opt-out"),
]


@pytest.mark.parametrize(("method", "path", "service", "internal"), TABLE)
def test_route_resolves_to_owning_service(
    method: str, path: str, service: str, internal: str
) -> None:
    route = resolve(path, method, get_settings())

    assert isinstance(route, Route)
    assert route.service == service
    assert httpx.URL(route.url).path == internal
    assert httpx.URL(route.url).port == PORTS[service]


@pytest.mark.parametrize("path", ["lens/metrics", "predictions/7", "forecasts"])
def test_l2_and_l3_resources_are_not_in_layer(path: str) -> None:
    assert isinstance(resolve(path, "GET", get_settings()), NotInLayer)


@pytest.mark.parametrize(
    "path",
    ["unknown", "internal/core/leads", "admin/users", "admin", "auth/validate", "analytics/market"],
)
def test_other_paths_do_not_resolve(path: str) -> None:
    assert resolve(path, "GET", get_settings()) is None


def test_wrong_method_on_fixed_routes() -> None:
    assert resolve("me", "POST", get_settings()) == "method_not_allowed"
    assert resolve("admin/tenant-config", "DELETE", get_settings()) == "method_not_allowed"


async def test_not_in_layer_is_a_404_with_reason(client: Any, world: World) -> None:
    response = await client.get("/api/v1/lens/metrics", headers=bearer(world.signer.token()))

    assert response.status_code == 404
    assert response.json() == {"detail": "not_in_layer"}
    assert world.core_requests() == []


async def test_public_requests_never_reach_an_analytics_service(client: Any, world: World) -> None:
    headers = bearer(world.signer.token())
    for path in ("market/summary", "segments", "ops/health", "territory/opportunity"):
        await client.get(f"/api/v1/{path}", headers=headers)
    hosts = {r.url.port for r in world.requests if r.url.path.startswith("/internal/")}
    assert hosts == {PORTS["pulse"]}


def test_gateway_sources_do_not_mention_the_retired_service() -> None:
    for file in (Path(gw.__file__).parent).glob("*.py"):
        assert "analytics" not in file.read_text().lower(), file.name


def test_compliance_opt_out_resolves_only_for_post_and_purge_stays_internal() -> None:
    settings = get_settings()
    assert resolve("compliance/opt-out", "GET", settings) == "method_not_allowed"
    assert resolve("compliance/purge", "POST", settings) is None
    assert resolve("compliance", "POST", settings) is None
