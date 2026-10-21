"""Every service-to-service sender signs a fresh token; receivers pin callers (REQ-SEC-005).

Senders: gateway, brief, ingest, seed, and the verify_live tool. Receivers: core's
per-route-group allow lists and identity's DRF permission. No network is used.
"""

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")
pytest.importorskip("structlog")
httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from libs.common.internal_auth import sign_service_token, verify_service_token  # noqa: E402
from libs.common.settings import get_settings  # noqa: E402
from ops import verify_live  # noqa: E402
from ops.seed.loader import HttpSeedCore  # noqa: E402
from services.brief.app.core_source import HttpCoreSource  # noqa: E402
from services.core.app import main as core_main  # noqa: E402
from services.identity.iam import permissions  # noqa: E402
from services.ingest.app.to_core import HttpCoreClient  # noqa: E402

SECRET = "client-test-secret"
TENANT = uuid4()
COMPANY = uuid4()


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _signer(request: httpx.Request) -> str | None:
    return verify_service_token(request.headers.get("X-Service-Token", ""), SECRET)


def _transport(seen: list[httpx.Request], payload: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------- senders


async def test_brief_client_signs_per_request_as_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    source = HttpCoreSource("http://core", SECRET)
    source._client = httpx.AsyncClient(
        base_url="http://core", transport=_transport(seen, {"version": 1, "rules": {}})
    )
    clock = {"now": 1000.0}
    monkeypatch.setattr("libs.common.internal_auth.time.time", lambda: clock["now"])
    await source.get_rules(TENANT)
    clock["now"] = 5000.0
    await source.get_rules(TENANT)
    await source.close()
    tokens = [r.headers["X-Service-Token"] for r in seen]
    assert tokens[0] != tokens[1]
    assert tokens[0] != SECRET
    assert verify_service_token(tokens[0], SECRET, now=1001) == "brief"
    assert verify_service_token(tokens[1], SECRET, now=5001) == "brief"
    assert verify_service_token(tokens[0], SECRET, now=5001) is None


async def test_ingest_client_signs_every_call_as_ingest() -> None:
    seen: list[httpx.Request] = []
    client = HttpCoreClient(
        "http://core",
        SECRET,
        client=httpx.AsyncClient(base_url="http://core", transport=_transport(seen, {})),
    )
    await client.upsert_company({"legal_name": "X"})
    await client.create_signal({"a": 1})
    await client.aclose()
    assert [_signer(r) for r in seen] == ["ingest", "ingest"]


async def test_seed_client_signs_as_seed_on_every_route() -> None:
    seen: list[httpx.Request] = []
    core = HttpSeedCore(
        "http://core",
        SECRET,
        client=httpx.AsyncClient(
            base_url="http://core", transport=_transport(seen, {"data": [], "next_cursor": None})
        ),
    )
    await core.upsert_company({"legal_name": "X"})
    await core.create_signal({"a": 1})
    await core.list_companies(10, None)
    await core.opt_out(str(COMPANY))
    await core.aclose()
    assert [r.url.path for r in seen] == [
        "/internal/core/companies/upsert",
        "/internal/core/signals",
        "/internal/core/companies",
        "/internal/core/compliance/opt-out",
    ]
    assert {_signer(r) for r in seen} == {"seed"}


def test_verify_live_signs_as_verify_live(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    class Answer:
        def __enter__(self) -> "Answer":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps({"data": []}).encode()

    def fake_urlopen(request: Any, timeout: float) -> Answer:
        seen.append(request)
        return Answer()

    monkeypatch.setattr(verify_live.urllib.request, "urlopen", fake_urlopen)
    backend = verify_live.LiveBackend({"INTERNAL_SERVICE_TOKEN": SECRET})
    backend._http("GET", "http://core/internal/core/leads?limit=5")
    token = seen[0].get_header("X-service-token")
    assert token != SECRET
    assert verify_service_token(token, SECRET) == "verify_live"


# --------------------------------------------------------------------------- core allow lists

TOKENS = ("gateway", "brief", "ingest", "seed", "verify_live", "pulse")
BODY_SIGNAL = {
    "company_id": str(COMPANY),
    "source": "maps",
    "signal_type": "listing",
    "observed_at": "2026-01-01T00:00:00Z",
    "tenant_id": str(TENANT),
}
BODY_COMPANY = {"legal_name": "Acme SAS", "nit": "900123456-7", "tenant_id": str(TENANT)}
LEAD = uuid4()

# (method, path, allowed callers)
ALLOW_LIST = [
    ("POST", "/internal/core/signals", {"gateway", "ingest", "seed"}),
    ("POST", "/internal/core/companies/upsert", {"gateway", "ingest", "seed"}),
    ("GET", "/internal/core/companies", {"gateway", "seed"}),
    ("POST", "/internal/core/compliance/opt-out", {"gateway", "seed"}),
    ("GET", "/internal/core/leads", {"gateway", "verify_live"}),
    ("GET", f"/internal/core/leads/{LEAD}", {"gateway", "brief"}),
    ("GET", f"/internal/core/companies/{COMPANY}/brief-facts", {"gateway", "brief"}),
    ("GET", "/internal/core/admin/tenant-config", {"gateway", "brief"}),
    ("PUT", "/internal/core/admin/tenant-config", {"gateway"}),
    ("POST", "/internal/core/leads", {"gateway"}),
    ("GET", "/internal/core/pipeline/kanban", {"gateway"}),
    ("GET", "/internal/core/projects", {"gateway"}),
    ("POST", "/internal/core/imports/companies", {"gateway"}),
    ("POST", "/internal/core/compliance/purge", {"gateway"}),
]


@pytest.mark.parametrize(("method", "path", "allowed"), ALLOW_LIST)
def test_core_route_groups_allow_only_listed_services(
    method: str, path: str, allowed: set[str]
) -> None:
    core_main.app.state.repository = object()
    client = TestClient(core_main.app, raise_server_exceptions=False)
    assert client.request(method, path).status_code == 401
    raw = client.request(method, path, headers={"X-Service-Token": SECRET})
    assert raw.status_code == 401
    expired = sign_service_token("gateway", SECRET, ttl_seconds=-5)
    assert client.request(method, path, headers={"X-Service-Token": expired}).status_code == 401
    for service in TOKENS:
        headers = {"X-Service-Token": sign_service_token(service, SECRET)}
        status = client.request(method, path, headers=headers).status_code
        assert (status != 401) == (service in allowed), (service, status)


class _RecordingRepository:
    """Records the tenant used to open the unit of work or to create the signal."""

    def __init__(self) -> None:
        self.tenants: list[Any] = []

    def company_unit_of_work(self, tenant_id: Any = None) -> Any:
        self.tenants.append(tenant_id)
        raise RuntimeError("stop after recording the tenant")

    async def create_signal(self, signal: Any, *, request_id: str | None) -> Any:
        self.tenants.append(signal.tenant_id)
        return uuid4(), uuid4()


@pytest.mark.parametrize("service", ["ingest", "seed"])
def test_ingest_and_seed_writes_take_tenant_context_from_the_body(service: str) -> None:
    repository = _RecordingRepository()
    core_main.app.state.repository = repository
    client = TestClient(core_main.app, raise_server_exceptions=False)
    headers = {
        "X-Service-Token": sign_service_token(service, SECRET),
        "X-Tenant-Id": str(uuid4()),  # ignored: only the body tenant counts
    }
    signal = client.post("/internal/core/signals", json=BODY_SIGNAL, headers=headers)
    assert signal.status_code == 201
    company = client.post("/internal/core/companies/upsert", json=BODY_COMPANY, headers=headers)
    assert company.status_code == 503
    assert repository.tenants == [TENANT, TENANT]


# --------------------------------------------------------------------------- identity


def _identity_allows(monkeypatch: pytest.MonkeyPatch, token: str | None) -> bool:
    monkeypatch.setattr(
        permissions, "settings", SimpleNamespace(IDENTITY_INTERNAL_SERVICE_TOKEN=SECRET)
    )
    headers = {} if token is None else {"X-Service-Token": token}
    request = SimpleNamespace(headers=headers)
    return permissions.InternalServiceToken().has_permission(request, None)  # type: ignore[arg-type]


def test_identity_permission_accepts_signed_tokens_only(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _identity_allows(monkeypatch, sign_service_token("gateway", SECRET))
    assert not _identity_allows(monkeypatch, SECRET)
    assert not _identity_allows(monkeypatch, sign_service_token("gateway", SECRET, ttl_seconds=-5))
    assert not _identity_allows(monkeypatch, sign_service_token("gateway", "other"))
    assert not _identity_allows(monkeypatch, None)


# --------------------------------------------------------------------------- configuration


def test_compose_gives_every_token_service_the_secret() -> None:
    from ops import check_configuration

    compose = check_configuration.COMPOSE_PATH.read_text(encoding="utf-8")
    assert check_configuration.services_missing_service_token(compose) == []
    broken = compose.replace("  INTERNAL_SERVICE_TOKEN:", "  OTHER_TOKEN:", 1)
    assert "core" in check_configuration.services_missing_service_token(broken)
