"""Check the Compose topology, dependencies, health probes, and credentials.

Parses ``docker-compose.yml`` without Docker and asserts the SPEC section 3.1
service catalog (REQ-ARC-001), migrate gating (REQ-DEP-001), and that the
gateway holds no database credentials (REQ-STR-002).
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

APIS = ["gateway", "identity", "ingest", "core", "pulse", "brief", "ml"]
WORKERS = {
    "core-relay": ("core", "services.core.app.outbox_relay"),
    "core-worker": ("core", "services.core.app.worker"),
    "ingest-processor": ("ingest", "services.ingest.app.processor"),
    "pulse-consumer": ("pulse", "services.pulse.app.consumer"),
    "ml-consumer": ("ml", "services.ml.app.consumer"),
}
DB_APIS = {"ingest": "ingest", "core": "core", "pulse": "pulse", "brief": "brief", "ml": "ml"}
EXPECTED = {"postgres", "redis", "migrate", "bootstrap-roles", "streamlit", "mocks", "n8n"} | set(APIS) | set(WORKERS)


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def _env(service: dict[str, Any]) -> dict[str, str]:
    return {k: str(v) for k, v in service.get("environment", {}).items()}


def _depends(service: dict[str, Any]) -> dict[str, str]:
    return {k: v["condition"] for k, v in service.get("depends_on", {}).items()}


def test_topology_matches_catalog(services: dict[str, Any]) -> None:
    assert set(services) == EXPECTED


def test_no_analytics_anywhere() -> None:
    assert "analytics" not in COMPOSE.read_text(encoding="utf-8").lower()


def test_migrate_is_one_shot_runner(services: dict[str, Any]) -> None:
    migrate = services["migrate"]
    assert migrate["command"] == ["python", "-m", "ops.migrate"]
    assert "MIGRATE_DATABASE_URL" in _env(migrate)
    assert migrate["restart"] == "no"
    assert _depends(migrate) == {"postgres": "service_healthy"}


def test_bootstrap_roles_is_one_shot_after_migrate(services: dict[str, Any]) -> None:
    boot = services["bootstrap-roles"]
    assert boot["build"] == services["migrate"]["build"]
    assert boot["command"] == ["python", "ops/bootstrap_roles.py"]
    assert boot["restart"] == "no"
    assert _depends(boot) == {
        "migrate": "service_completed_successfully",
        "postgres": "service_healthy",
    }
    env = _env(boot)
    assert env["BOOTSTRAP_DATABASE_URL"].startswith("postgresql://")
    for schema in DB_APIS.values():
        assert env[f"BOOTSTRAP_PASSWORD_{schema.upper()}"].endswith(f":-CHANGE_ME_{schema.upper()}}}")


def test_dockerfile_installs_psycopg_for_bootstrap() -> None:
    dockerfile = COMPOSE.parent / "deploy" / "Dockerfile.python"
    assert "psycopg" in dockerfile.read_text(encoding="utf-8")


def test_postgres_does_not_apply_migrations(services: dict[str, Any]) -> None:
    mounts = services["postgres"]["volumes"]
    assert not any("initdb" in str(m) or "migrations" in str(m) for m in mounts)


@pytest.mark.parametrize("name", [*DB_APIS, *WORKERS])
def test_database_services_wait_for_migrate(services: dict[str, Any], name: str) -> None:
    deps = _depends(services[name])
    assert deps["migrate"] == "service_completed_successfully"
    assert deps["bootstrap-roles"] == "service_completed_successfully"
    assert deps["postgres"] == "service_healthy"
    assert deps["redis"] == "service_healthy"


@pytest.mark.parametrize(
    ("name", "schema"), [*DB_APIS.items(), *((n, s) for n, (s, _) in WORKERS.items())]
)
def test_each_service_uses_its_own_role(services: dict[str, Any], name: str, schema: str) -> None:
    url = _env(services[name])["DATABASE_URL"]
    assert f"//{schema}_app:" in url
    assert "postgres:postgres" not in url


@pytest.mark.parametrize(("name", "module"), [(n, m) for n, (_, m) in WORKERS.items()])
def test_worker_commands(services: dict[str, Any], name: str, module: str) -> None:
    assert services[name]["command"] == ["python", "-m", module]
    assert (Path(__file__).resolve().parents[2] / (module.replace(".", "/") + ".py")).exists()


@pytest.mark.parametrize("name", APIS)
def test_apis_have_health_probes(services: dict[str, Any], name: str) -> None:
    health = services[name]["healthcheck"]
    assert "/health" in " ".join(health["test"])
    assert health["retries"] and health["timeout"]


def test_streamlit_is_real_container(services: dict[str, Any]) -> None:
    streamlit = services["streamlit"]
    command = streamlit["command"]
    assert command[:2] == ["streamlit", "run"]
    assert "uvicorn" not in command
    assert "/_stcore/health" in " ".join(streamlit["healthcheck"]["test"])
    assert not any("DATABASE" in key for key in _env(streamlit))


def test_gateway_has_no_credentials(services: dict[str, Any]) -> None:
    gateway = services["gateway"]
    env = _env(gateway)
    assert not [k for k in env if "DATABASE" in k or "POSTGRES" in k]
    assert "ANALYTICS_URL" not in env
    assert "postgres" not in _depends(gateway)
    assert "migrate" not in _depends(gateway)
    assert "analytics" not in _depends(gateway)
    assert not any("postgres" in v.lower() for v in env.values())


def test_app_services_read_optional_env_file(services: dict[str, Any]) -> None:
    for name in [*APIS, "streamlit", *WORKERS, "migrate"]:
        if name == "migrate":
            continue
        entries = services[name]["env_file"]
        assert {"path": ".env", "required": False} in entries
