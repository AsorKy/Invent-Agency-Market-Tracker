"""Check the OpenAPI document against the route registry and the gateway (REQ-API-001)."""

import importlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")
pytest.importorskip("structlog")
pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from libs.common.settings import get_settings  # noqa: E402
from services.gateway.app import registry  # noqa: E402
from services.gateway.app.openapi_doc import API_PREFIX, build_openapi, render_openapi  # noqa: E402
from services.gateway.app.routes import _IDENTITY_ROUTES, Route, resolve  # noqa: E402

COMMITTED = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"

# Internal service routes that are deliberately not public, with the reason.
NOT_PUBLIC = {
    ("GET", "/internal/pulse/market/overview"): "alias of /pulse/market",
    ("GET", "/internal/brief/companies/{company_id}"): "company briefs are not public yet",
    ("GET", "/internal/brief/companies/{company_id}/digest"): "company briefs are not public yet",
    ("GET", "/internal/brief/leads/{lead_id}/digest"): "alias of the lead brief",
    ("GET", "/internal/core/companies/{company_id}/brief-facts"): "brief service only, never public",
    ("POST", "/internal/core/compliance/opt-out"): "n8n and ops only, never public",
    ("POST", "/internal/core/compliance/purge"): "n8n and ops only, never public",
    ("POST", "/internal/pulse/ingest/profiles"): "ml service only, never public",
    ("POST", "/internal/pulse/ingest/communities"): "ml service only, never public",
    ("POST", "/internal/pulse/ingest/topics"): "ml service only, never public",
    ("POST", "/internal/pulse/ingest/attribution"): "ml service only, never public",
    ("GET", "/internal/pulse/projects/metrics"): "lens service calls only, never public",
    ("POST", "/internal/pulse/socioeconomic/refresh"): "n8n only, never public",
}
SERVICES = ["core", "identity", "pulse", "brief", "ml"]
STATIC_INTERNAL = {"/internal/metrics"}


def _doc() -> dict:
    return build_openapi()


def _concrete(path: str) -> str:
    return path.strip("/")


def test_committed_file_is_not_stale() -> None:
    assert COMMITTED.exists(), "run make openapi"
    assert COMMITTED.read_text(encoding="utf-8") == render_openapi()


def test_rendering_is_deterministic() -> None:
    assert render_openapi() == render_openapi()
    json.loads(render_openapi())


def test_every_registry_route_is_in_the_document() -> None:
    paths = json.loads(COMMITTED.read_text(encoding="utf-8"))["paths"]
    for route in registry.PUBLIC_ROUTES:
        assert route.method.lower() in paths.get(API_PREFIX + route.path, {}), route.key


def test_registry_has_no_duplicates() -> None:
    keys = [route.key for route in registry.PUBLIC_ROUTES]
    assert len(keys) == len(set(keys))


def test_every_registry_route_resolves_in_the_gateway_table() -> None:
    settings = get_settings()
    for route in registry.PUBLIC_ROUTES:
        resolved = resolve(_concrete(route.path), route.method, settings)
        assert isinstance(resolved, Route), route.key


def test_identity_routes_and_methods_match_the_gateway_table() -> None:
    documented = {r.key for r in registry.PUBLIC_ROUTES}
    for path, methods in _IDENTITY_ROUTES.items():
        for method in methods:
            assert (method, "/" + path) in documented


def test_internal_routes_never_documented() -> None:
    for path in _doc()["paths"]:
        assert path.startswith(API_PREFIX + "/")
        assert "/internal" not in path


def test_every_service_route_is_public_or_explicitly_not_public() -> None:
    settings = get_settings()
    targets: dict[tuple[str, str], tuple[str, str]] = {}
    for route in registry.PUBLIC_ROUTES:
        resolved = resolve(_concrete(route.path), route.method, settings)
        assert isinstance(resolved, Route)
        targets[(route.method, unquote(urlsplit(resolved.url).path))] = route.key
    missing: list[tuple[str, str]] = []
    for service in SERVICES:
        module = importlib.import_module(f"services.{service}.app.main")
        for app_route in module.app.routes:
            path = getattr(app_route, "path", "")
            if not path.startswith("/internal/") or path in STATIC_INTERNAL:
                continue
            for method in getattr(app_route, "methods", set()) - {"HEAD", "OPTIONS"}:
                key = (method, path)
                if key not in targets and key not in NOT_PUBLIC:
                    missing.append(key)
    assert not missing, f"routes missing from the registry: {missing}"


def test_required_headers_are_documented() -> None:
    doc = _doc()["paths"]
    pulse = doc[API_PREFIX + "/pulse/pipeline-backlog"]["get"]["responses"]["200"]
    assert "X-As-Of" in pulse["headers"]
    brief = doc[API_PREFIX + "/leads/{lead_id}/brief"]["get"]["responses"]["200"]
    assert "X-Brief-Cache" in brief["headers"]
    config = doc[API_PREFIX + "/admin/tenant-config"]["get"]["responses"]["200"]
    assert "ETag" in config["headers"]
    create = doc[API_PREFIX + "/leads"]["post"]
    assert any(p["name"] == "Idempotency-Key" and p["in"] == "header" for p in create["parameters"])


def test_error_bodies_and_schemas_present() -> None:
    doc = _doc()
    op = doc["paths"][API_PREFIX + "/leads"]["get"]
    assert "401" in op["responses"] and "429" in op["responses"]
    schemas = doc["components"]["schemas"]
    for name in ("ErrorBody", "SignalCreate", "BriefResponse", "PipelineBacklogResponse"):
        assert name in schemas
    backlog = schemas["PipelineBacklogResponse"]
    assert "capacity_estimate" in backlog["properties"]
    assert "capacity_estimate" not in backlog["required"]
