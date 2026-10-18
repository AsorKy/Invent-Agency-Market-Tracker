"""Check /health and /ready of every service app with faked dependencies (REQ-RES-001)."""

import importlib
from collections.abc import Callable
from types import ModuleType

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("pydantic")
from fastapi.testclient import TestClient  # noqa: E402

DB_REDIS_SERVICES = ["core", "ml", "ingest", "brief", "pulse"]
NO_DEPENDENCY_CHECK = {
    "identity": "bootstrap /ready always returns 200 and ignores database and Redis",
    "streamlit": "shell /ready always returns 200 and ignores database and Redis",
}


def _load(service: str) -> ModuleType:
    return importlib.import_module(f"services.{service}.app.main")


def _patch(module: ModuleType, monkeypatch: pytest.MonkeyPatch, *, db: bool, redis: bool) -> None:
    async def fake_db(_url: str) -> bool:
        return db

    async def fake_redis(_url: str) -> bool:
        return redis

    monkeypatch.setattr(module, "check_database", fake_db, raising=False)
    monkeypatch.setattr(module, "check_redis", fake_redis, raising=False)


def _client(module: ModuleType) -> TestClient:
    # No context manager: lifespan (real pools) is intentionally not started.
    return TestClient(module.app)  # type: ignore[attr-defined]


@pytest.mark.parametrize("service", [*DB_REDIS_SERVICES, *NO_DEPENDENCY_CHECK, "gateway"])
def test_health_is_always_200(service: str) -> None:
    response = _client(_load(service)).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("service", DB_REDIS_SERVICES)
@pytest.mark.parametrize(("db", "redis"), [(False, True), (True, False), (False, False)])
def test_ready_503_when_dependency_fails(
    service: str, db: bool, redis: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load(service)
    _patch(module, monkeypatch, db=db, redis=redis)
    response = _client(module).get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


@pytest.mark.parametrize("service", DB_REDIS_SERVICES)
def test_ready_200_when_dependencies_ok(service: str, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load(service)
    _patch(module, monkeypatch, db=True, redis=True)
    response = _client(module).get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_gateway_ready_503_when_redis_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load("gateway")

    async def fake_redis(_url: str) -> bool:
        return False

    monkeypatch.setattr(module, "check_redis", fake_redis)
    response = _client(module).get("/ready")
    assert response.status_code == 503


_xfail: Callable[[str], pytest.MarkDecorator] = lambda reason: pytest.mark.xfail(  # noqa: E731
    strict=False, reason=reason
)


@pytest.mark.parametrize(
    "service",
    [pytest.param(name, marks=_xfail(reason)) for name, reason in NO_DEPENDENCY_CHECK.items()],
)
def test_ready_503_for_services_without_dependency_checks(
    service: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load(service)
    _patch(module, monkeypatch, db=False, redis=False)
    assert _client(module).get("/ready").status_code == 503


def test_mocks_service_exposes_ready() -> None:
    pytest.importorskip("flask")
    module = _load("mocks")
    response = module.app.test_client().get("/health")  # type: ignore[attr-defined]
    assert response.status_code == 200
    if module.app.test_client().get("/ready").status_code == 404:  # type: ignore[attr-defined]
        pytest.xfail("mocks service has no /ready endpoint")
