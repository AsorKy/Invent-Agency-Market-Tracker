"""Check the Compose topology, dependencies, health probes, and credentials.

Parses ``docker-compose.yml`` without Docker and asserts the SPEC section 3.1
service catalog (REQ-ARC-001), migrate gating (REQ-DEP-001), and that the
gateway holds no database credentials (REQ-STR-002).
"""

import json
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
DEV_TOOLS = {"pgadmin"}  # profile-gated helpers, outside the SPEC section 3.1 catalog
EXPECTED = (
    {"postgres", "redis", "migrate", "bootstrap-roles", "streamlit", "mocks", "n8n"}
    | set(APIS)
    | set(WORKERS)
    | DEV_TOOLS
)


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
    for schema in [*DB_APIS.values(), "identity", "socio"]:
        assert env[f"BOOTSTRAP_PASSWORD_{schema.upper()}"].endswith(
            f":-CHANGE_ME_{schema.upper()}}}"
        )


def test_dockerfile_installs_psycopg_for_bootstrap() -> None:
    dockerfile = COMPOSE.parent / "deploy" / "Dockerfile.python"
    assert "psycopg" in dockerfile.read_text(encoding="utf-8")


def test_postgres_image_carries_every_bootstrap_extension(services: dict[str, Any]) -> None:
    from ops.schemas import EXTENSIONS

    postgres = services["postgres"]
    assert postgres["build"] == {"context": "deploy", "dockerfile": "Dockerfile.postgres"}
    dockerfile = (COMPOSE.parent / "deploy" / "Dockerfile.postgres").read_text(encoding="utf-8")
    packages = {"vector": "pgvector", "postgis": "postgis", "pg_cron": "cron"}
    builtin = {"pg_trgm", "pgcrypto"}  # contrib, shipped with the base image
    assert set(EXTENSIONS) <= set(packages) | builtin
    for extension in set(EXTENSIONS) - builtin:
        assert f"postgresql-16-{packages[extension]}" in dockerfile
    assert dockerfile.count("FROM postgres:16") == 1


def test_postgres_image_build_retries_flaky_apt_downloads() -> None:
    dockerfile = (COMPOSE.parent / "deploy" / "Dockerfile.postgres").read_text(encoding="utf-8")
    assert "Acquire::Retries" in dockerfile
    assert dockerfile.index("Acquire::Retries") < dockerfile.index("apt-get update")


def test_postgres_preloads_pg_cron_on_the_application_database(services: dict[str, Any]) -> None:
    command = services["postgres"]["command"]
    assert command[0] == "postgres"
    assert "shared_preload_libraries=pg_cron" in command
    assert "cron.database_name=${POSTGRES_DB:-invent_market_tracker}" in command


def test_pgadmin_is_an_optional_loopback_ui(services: dict[str, Any]) -> None:
    pgadmin = services["pgadmin"]
    assert pgadmin["profiles"] == ["ui"]
    assert all(port.startswith("127.0.0.1:") for port in pgadmin["ports"])
    assert _depends(pgadmin) == {"postgres": "service_healthy"}
    assert "postgres" not in " ".join(_env(pgadmin)).lower()
    servers = json.loads(
        (COMPOSE.parent / "deploy" / "pgadmin" / "servers.json").read_text("utf-8")
    )
    server = servers["Servers"]["1"]
    assert server["Host"] == "postgres" and server["Username"] == "postgres"
    assert "Password" not in server and "PassFile" not in server
    assert not any("pgadmin" in _depends(other) for other in services.values())


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


def test_identity_reaches_postgres_with_its_own_role(services: dict[str, Any]) -> None:
    identity = services["identity"]
    env = _env(identity)
    assert env["POSTGRES_HOST"] == "postgres"
    assert env["IDENTITY_DB_USER"].endswith(":-identity_app}")
    assert env["IDENTITY_DB_PASSWORD"] == "${BOOTSTRAP_PASSWORD_IDENTITY:-CHANGE_ME_IDENTITY}"
    assert "POSTGRES_PASSWORD" not in env
    deps = _depends(identity)
    assert deps["migrate"] == "service_completed_successfully"
    assert deps["bootstrap-roles"] == "service_completed_successfully"
    assert deps["postgres"] == "service_healthy"


def test_bootstrap_roles_declares_profile_and_all_passwords(services: dict[str, Any]) -> None:
    env = _env(services["bootstrap-roles"])
    assert env["APP_PROFILE"].endswith(":-local}")
    assert env["ENVIRONMENT"].endswith(":-local}")
    assert env["BOOTSTRAP_PASSWORD_SOCIO"].endswith(":-CHANGE_ME_SOCIO}")


def test_configuration_guard_reports_missing_role_wiring() -> None:
    from ops import check_configuration as guard

    text = COMPOSE.read_text(encoding="utf-8")
    env = guard._env_example_variables()
    assert guard.role_wiring_problems(text, env) == []
    stripped = text.replace("IDENTITY_DB_USER:", "OTHER_USER:")
    assert any("IDENTITY_DB_USER" in p for p in guard.role_wiring_problems(stripped, env))
    assert any("BOOTSTRAP_PASSWORD_SOCIO" in p for p in guard.role_wiring_problems(text, set()))
