"""Gateway exposure of the funnel and validation routes (T-148, REQ-API-001, REQ-LNS-013)."""

from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("structlog")
pytest.importorskip("jwt")
pytest.importorskip("cryptography")

import test_gateway_auth as base  # noqa: E402
from test_gateway_auth import World, bearer  # noqa: E402

client = base.client
world = base.world

from libs.common.settings import get_settings  # noqa: E402
from services.gateway.app.routes import NotInLayer, Route, resolve  # noqa: E402

PID = "11111111-1111-1111-1111-111111111111"
READS = [
    f"funnel/projects/{PID}",
    f"validation/projects/{PID}/financial",
    f"validation/projects/{PID}/meta-ml",
    "validation/meta-ml",
]
DECISION = f"funnel/projects/{PID}/gates/G1/decision"


@pytest.mark.parametrize("path", [*READS, DECISION])
def test_routes_resolve_to_core_internal(path: str) -> None:
    method = "POST" if path == DECISION else "GET"
    route = resolve(path, method, get_settings())
    assert isinstance(route, Route)
    assert route.service == "core"
    assert route.url.endswith("/internal/core/" + path)
    assert route.allowed_roles is not None


def test_decision_is_narrower_than_reads() -> None:
    read = resolve(READS[0], "GET", get_settings())
    decide = resolve(DECISION, "POST", get_settings())
    assert isinstance(read, Route) and isinstance(decide, Route)
    assert read.allowed_roles == {"admin", "strategist", "analyst"}
    assert decide.allowed_roles == {"admin", "strategist"}


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("validation/runs", "GET"),
        (f"validation/projects/{PID}/financial/extra", "GET"),
        (f"validation/projects/{PID}/financial", "POST"),
        (DECISION, "GET"),
        (f"funnel/projects/{PID}/gates/G1", "POST"),
        ("funnel/gates", "GET"),
    ],
)
def test_other_paths_do_not_resolve(path: str, method: str) -> None:
    result = resolve(path, method, get_settings())
    assert result is None and not isinstance(result, NotInLayer)


async def test_reads_allow_analyst_and_deny_other_roles(client: Any, world: World) -> None:
    ok = await client.get(f"/api/v1/{READS[1]}", headers=bearer(world.signer.token(role="analyst")))
    assert ok.status_code == 200
    denied = await client.get(
        f"/api/v1/{READS[1]}", headers=bearer(world.signer.token(role="bd_rep"))
    )
    assert denied.status_code == 403


async def test_decision_denies_analyst_and_forwards_tenant(client: Any, world: World) -> None:
    body = b'{"inputs": {}, "expected_version": 1}'
    analyst = bearer(world.signer.token(role="analyst"))
    strategist = bearer(world.signer.token(role="strategist"))
    url = f"/api/v1/{DECISION}"
    assert (await client.post(url, content=body, headers=analyst)).status_code == 403
    assert world.core_requests() == []
    assert (await client.post(url, content=body, headers=strategist)).status_code == 200
    sent = world.core_requests()[0]
    assert sent.headers["x-user-role"] == "strategist"
    assert sent.url.path == "/internal/core/" + DECISION


INPUTS = f"projects/{PID}/validation-inputs"


def test_inputs_route_resolves_for_put_only() -> None:
    route = resolve(INPUTS, "PUT", get_settings())
    assert isinstance(route, Route)
    assert route.url.endswith("/internal/core/" + INPUTS)
    assert route.allowed_roles == {"admin", "strategist"}
    assert resolve(INPUTS, "GET", get_settings()) == "method_not_allowed"


async def test_inputs_denies_other_roles_and_allows_strategist(client: Any, world: World) -> None:
    url = f"/api/v1/{INPUTS}"
    for role in ("analyst", "bd_rep"):
        denied = await client.put(
            url, json={}, headers={**bearer(world.signer.token(role=role)), "If-Match": '"0"'}
        )
        assert denied.status_code == 403
        assert denied.json() == {"detail": "forbidden"}
    assert world.core_requests() == []
    for role in ("strategist", "admin"):
        ok = await client.put(
            url, json={}, headers={**bearer(world.signer.token(role=role)), "If-Match": '"0"'}
        )
        assert ok.status_code == 200
    sent = world.core_requests()[0]
    assert sent.url.path == "/internal/core/" + INPUTS
    assert sent.headers["x-user-role"] == "strategist"
    assert sent.headers["if-match"] == '"0"'
