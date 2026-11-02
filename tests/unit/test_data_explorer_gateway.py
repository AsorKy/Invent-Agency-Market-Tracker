"""Gateway side of the read-only data explorer (REQ-API-002, INV-01).

``/api/v1/data/catalog`` fans out to the five owning services, keeps only the datasets each
service owns, and lists a service that does not answer with ``unavailable``;
``/api/v1/data/<dataset>`` goes to the owner of the id with the read roles. The ``pulse/``,
``brief/`` and ``ingest/`` passthroughs and dot segments never reach the internal data routes.
Reuses the fake upstreams of ``test_gateway_auth``; no network, Redis, or Postgres is used.
"""

from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
pytest.importorskip("structlog")
pytest.importorskip("jwt")
pytest.importorskip("cryptography")

import test_gateway_auth as base  # noqa: E402
from test_gateway_auth import TENANT_A, TENANT_B, Signer, World, bearer, install  # noqa: E402

from libs.common.data_explorer import CATALOG, DATA_SERVICES, DatasetInfo  # noqa: E402
from libs.common.settings import get_settings  # noqa: E402
from services.gateway.app import main as gw  # noqa: E402
from services.gateway.app.registry import DataCatalog  # noqa: E402
from services.gateway.app.routes import Route, resolve  # noqa: E402

client = base.client
READ_ROLES = frozenset({"admin", "strategist", "analyst"})


def _entry(info: DatasetInfo, count: int = 5) -> dict[str, Any]:
    return {
        "id": info.id,
        "title": info.title,
        "description": info.description,
        "service": info.service,
        "origin": info.origin,
        "count": count,
        "columns": [{"name": "id", "type": "uuid"}],
    }


class DataWorld(World):
    """Fake upstreams whose ``/internal/<service>/data/catalog`` answers can fail."""

    def __init__(self, signer: Signer) -> None:
        super().__init__(signer)
        self.down: set[str] = set()
        self.broken: set[str] = set()

    def __call__(self, request: Any) -> Any:
        path = request.url.path
        if not path.endswith("/data/catalog"):
            return super().__call__(request)
        self.requests.append(request)
        service = path.split("/")[2]
        if service in self.down:
            raise httpx.ConnectError("service down", request=request)
        if service in self.broken:
            return httpx.Response(500, json={"detail": "boom"})
        entries = [_entry(info) for info in CATALOG.values() if info.service == service]
        if service == "brief":
            # A service can never claim a dataset it does not own.
            entries.append(_entry(CATALOG["companies"], count=999))
        return httpx.Response(200, json={"datasets": entries})

    def catalog_requests(self) -> list[Any]:
        return [r for r in self.requests if r.url.path.endswith("/data/catalog")]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> DataWorld:
    fake = DataWorld(Signer())
    install(monkeypatch, fake)
    base.configure(monkeypatch)
    return fake


def _analyst(world: World, **claims: Any) -> dict[str, str]:
    return bearer(world.signer.token(role=claims.pop("role", "analyst"), **claims))


# --------------------------------------------------------------------------- #
# Route table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("dataset_id", list(CATALOG))
def test_every_dataset_resolves_to_its_owner_with_the_read_roles(dataset_id: str) -> None:
    settings = get_settings()
    owner = CATALOG[dataset_id].service
    route = resolve(f"data/{dataset_id}", "GET", settings)

    assert isinstance(route, Route)
    assert route.service == owner and route.allowed_roles == READ_ROLES
    assert not route.admin_only and route.fanout == ()
    base_url = getattr(settings, f"{owner}_url").rstrip("/")
    assert route.url == base_url + f"/internal/{owner}/data/{dataset_id}"


def test_catalog_resolves_to_a_fan_out_over_the_five_services() -> None:
    settings = get_settings()
    route = resolve("data/catalog", "GET", settings)

    assert isinstance(route, Route)
    assert route.allowed_roles == READ_ROLES and route.service == "gateway"
    assert route.fanout == tuple(
        (
            service,
            getattr(settings, f"{service}_url").rstrip("/") + f"/internal/{service}/data/catalog",
        )
        for service in DATA_SERVICES
    )


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_data_explorer_is_read_only(method: str) -> None:
    for path in ("data/catalog", "data/companies"):
        assert resolve(path, method, get_settings()) == "method_not_allowed"


@pytest.mark.parametrize(
    "path",
    [
        "data",
        "data/unknown",
        "data/catalog/extra",
        "data/companies/1",
        "pulse/data/fact_signals",
        "pulse/data/catalog",
        "brief/data/record_digests",
        "ingest/data/raw_snapshots",
        "pulse/./data/fact_signals",
        "pulse/market/../data/fact_signals",
        "leads/../data/companies/brief",
    ],
)
def test_other_ways_to_the_internal_data_routes_do_not_resolve(path: str) -> None:
    assert resolve(path, "GET", get_settings()) is None


def test_the_passthroughs_still_resolve_their_own_paths() -> None:
    settings = get_settings()
    for path in ("pulse/market", "brief/companies/7", "ingest/webhook/bd", "pulse/database"):
        assert isinstance(resolve(path, "GET", settings), Route), path


# --------------------------------------------------------------------------- #
# Proxy and fan-out
# --------------------------------------------------------------------------- #


async def test_catalog_merges_every_service_in_catalog_order(client: Any, world: DataWorld) -> None:
    response = await client.get("/api/v1/data/catalog", headers=_analyst(world))

    assert response.status_code == 200
    datasets = DataCatalog.model_validate(response.json()).datasets
    assert [d.id for d in datasets] == list(CATALOG)
    assert {d.unavailable for d in datasets} == {False}
    assert {d.count for d in datasets} == {5}
    sent = world.catalog_requests()
    assert sorted(r.url.path.split("/")[2] for r in sent) == sorted(DATA_SERVICES)
    assert {r.headers["x-tenant-id"] for r in sent} == {TENANT_A}
    assert {r.headers["x-user-role"] for r in sent} == {"analyst"}
    assert all(r.headers["x-service-token"].startswith("v1.gateway.") for r in sent)


async def test_a_down_or_failing_service_is_listed_as_unavailable(
    client: Any, world: DataWorld
) -> None:
    world.down.add("ml")
    world.broken.add("pulse")

    response = await client.get("/api/v1/data/catalog", headers=_analyst(world))

    assert response.status_code == 200
    by_id = {d["id"]: d for d in response.json()["datasets"]}
    for dataset_id in ("lead_scores", "fact_signals", "daily_rollups"):
        info = CATALOG[dataset_id]
        assert by_id[dataset_id] == {
            "id": dataset_id,
            "title": info.title,
            "description": info.description,
            "service": info.service,
            "origin": info.origin,
            "count": None,
            "columns": [],
            "unavailable": True,
        }
    assert by_id["companies"]["count"] == 5 and by_id["companies"]["unavailable"] is False
    assert len(by_id) == len(CATALOG)


async def test_a_red_service_is_not_called(
    client: Any, world: DataWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gw.readiness, "state", {"core": False})

    body = (await client.get("/api/v1/data/catalog", headers=_analyst(world))).json()

    assert not [r for r in world.catalog_requests() if r.url.port == 8002]
    unavailable = {d["id"] for d in body["datasets"] if d["unavailable"]}
    assert unavailable == {"companies", "signals", "leads"}


@pytest.mark.parametrize("role", ["intake", "bd_rep"])
async def test_the_data_explorer_needs_a_read_role(
    client: Any, world: DataWorld, role: str
) -> None:
    headers = _analyst(world, role=role)

    for path in ("/api/v1/data/catalog", "/api/v1/data/companies"):
        response = await client.get(path, headers=headers)
        assert response.status_code == 403

    assert [r for r in world.requests if "/data/" in r.url.path] == []


async def test_validation_on_needs_a_token(client: Any, world: DataWorld) -> None:
    response = await client.get("/api/v1/data/catalog", headers={"X-Tenant-Id": TENANT_B})

    assert response.status_code == 401
    assert world.catalog_requests() == []


async def test_a_dataset_page_goes_to_its_owner_with_the_query(
    client: Any, world: DataWorld
) -> None:
    params = {"limit": "5", "cursor": "abc", "q": "rule"}
    response = await client.get(
        "/api/v1/data/lead_scores",
        params=params,
        headers={**_analyst(world, role="strategist"), "X-Tenant-Id": TENANT_B},
    )

    assert response.status_code == 200
    (sent,) = [r for r in world.requests if r.url.path.startswith("/internal/")]
    assert sent.url.path == "/internal/ml/data/lead_scores" and sent.url.port == 8004
    assert dict(sent.url.params) == params
    assert sent.headers["x-tenant-id"] == TENANT_A


async def test_unknown_datasets_and_writes_are_refused(client: Any, world: DataWorld) -> None:
    headers = _analyst(world, role="admin")

    unknown = await client.get("/api/v1/data/nope", headers=headers)
    write = await client.post("/api/v1/data/companies", headers=headers, content=b"{}")

    assert unknown.status_code == 404 and unknown.json() == {"detail": "route_not_found"}
    assert write.status_code == 405
    assert [r for r in world.requests if r.url.path.startswith("/internal/")] == []
