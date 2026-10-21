"""Unit tests for ops/bootstrap_env.py without Docker or network (REQ-RUN-001)."""

import itertools
from collections.abc import Callable
from pathlib import Path

import pytest

from ops import bootstrap_env as env

TEMPLATE = (
    "POSTGRES_USER=postgres\n"
    "POSTGRES_PASSWORD=postgres\n"
    "DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/db\n"
    "EDGE_SECRET=\n"
    "INTERNAL_SERVICE_TOKEN=local-development-token\n"
    "BOOTSTRAP_PASSWORD_CORE=\n"
    "DEV_USER_PASSWORD_ADMIN=\n"
    "LOG_LEVEL=INFO\n"
    "N8N_ENCRYPTION_KEY=keep-me\n"
)


def _generator() -> Callable[[], str]:
    numbers = itertools.count(1)
    return lambda: f"gen{next(numbers)}"


def test_fill_secrets_replaces_only_placeholders() -> None:
    text = env.fill_secrets(TEMPLATE, _generator())
    values = {k: v for k, _, v in (line.partition("=") for line in text.splitlines())}
    assert values["POSTGRES_PASSWORD"] == "gen1"
    assert values["EDGE_SECRET"] == "gen2"
    assert values["INTERNAL_SERVICE_TOKEN"] == "gen3"
    assert values["BOOTSTRAP_PASSWORD_CORE"].startswith("gen")
    assert values["DEV_USER_PASSWORD_ADMIN"].startswith("gen")
    assert values["LOG_LEVEL"] == "INFO"
    assert values["N8N_ENCRYPTION_KEY"] == "keep-me"
    assert values["DATABASE_URL"] == "postgresql+asyncpg://postgres:gen1@postgres:5432/db"


def test_ensure_env_creates_once_and_never_overwrites(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    target = tmp_path / ".env"
    template.write_text(TEMPLATE, encoding="utf-8")
    assert env.ensure_env(template, target, _generator()) is True
    first = target.read_text(encoding="utf-8")
    assert "EDGE_SECRET=gen" in first
    assert target.stat().st_mode & 0o077 == 0
    assert env.ensure_env(template, target, _generator()) is False
    assert target.read_text(encoding="utf-8") == first


def test_ensure_env_does_not_print_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    template = tmp_path / ".env.example"
    template.write_text(TEMPLATE, encoding="utf-8")
    env.ensure_env(template, tmp_path / ".env", lambda: "topsecretvalue")
    captured = capsys.readouterr()
    assert "topsecretvalue" not in captured.out + captured.err


def test_repository_template_gets_every_secret_key() -> None:
    text = env.fill_secrets(env.TEMPLATE.read_text(encoding="utf-8"), lambda: "x" * 8)
    values = {k: v for k, _, v in (line.partition("=") for line in text.splitlines())}
    for key in env.SECRET_KEYS:
        assert values[key] == "x" * 8


def test_service_urls_use_ports() -> None:
    urls = env.service_urls({"GATEWAY_PORT": "9000"})
    assert urls["Gateway"] == "http://localhost:9000"
    assert urls["Streamlit"] == "http://localhost:8501"
    assert "n8n" in urls


def test_wait_ready_succeeds_after_retries() -> None:
    answers = iter([False, False, True])
    slept: list[float] = []
    assert env.wait_ready("u", 100, 1, lambda _u: next(answers), slept.append, lambda: 0.0)
    assert slept == [1, 1]


def test_wait_ready_times_out() -> None:
    ticks = itertools.count(0, 5)
    assert not env.wait_ready(
        "u", 10, 1, lambda _u: False, lambda _s: None, lambda: float(next(ticks))
    )


def test_bootstrap_roles_profile_detection() -> None:
    from ops import bootstrap_roles as roles

    assert roles.is_local_profile({})
    assert roles.is_local_profile({"APP_PROFILE": "local", "ENVIRONMENT": "ci"})
    assert not roles.is_local_profile({"APP_PROFILE": "production"})
    assert not roles.is_local_profile({"ENVIRONMENT": "staging"})


def test_bootstrap_roles_flags_default_or_empty_passwords() -> None:
    from ops import bootstrap_roles as roles

    passwords = {"identity": "s3cret-value", "socio": "CHANGE_ME_SOCIO", "core": "  "}
    weak = roles.insecure_password_schemas(passwords, ["identity", "socio", "core", "ml"])
    assert weak == ["socio", "core", "ml"]


def test_bootstrap_roles_refuses_defaults_outside_local(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ops import bootstrap_roles as roles

    for key in list(roles.os.environ):
        if key.startswith("BOOTSTRAP_PASSWORD_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("APP_PROFILE", "production")
    monkeypatch.setenv("BOOTSTRAP_PASSWORD_IDENTITY", "CHANGE_ME_IDENTITY")
    assert roles.main([]) == 2
    err = capsys.readouterr().err
    assert "BOOTSTRAP_PASSWORD_IDENTITY" in err and "BOOTSTRAP_PASSWORD_SOCIO" in err
    assert "CHANGE_ME_IDENTITY" not in err


def test_bootstrap_roles_prints_sql_with_real_passwords_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ops import bootstrap_roles as roles

    monkeypatch.setenv("APP_PROFILE", "production")
    for schema in roles.SCHEMAS:
        monkeypatch.setenv(f"BOOTSTRAP_PASSWORD_{schema.upper()}", f"pw-{schema}-x9")
    monkeypatch.delenv("BOOTSTRAP_DATABASE_URL", raising=False)
    assert roles.main([]) == 0
    out = capsys.readouterr().out
    assert "pw-identity-x9" in out and "CHANGE_ME" not in out


def test_bootstrap_roles_local_profile_keeps_placeholders(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ops import bootstrap_roles as roles

    for key in list(roles.os.environ):
        if key.startswith("BOOTSTRAP_PASSWORD_") or key in {"APP_PROFILE", "ENVIRONMENT"}:
            monkeypatch.delenv(key)
    monkeypatch.delenv("BOOTSTRAP_DATABASE_URL", raising=False)
    assert roles.main([]) == 0
    assert "CHANGE_ME_IDENTITY" in capsys.readouterr().out


def test_identity_socio_and_redis_passwords_are_generated() -> None:
    for key in (
        "BOOTSTRAP_PASSWORD_IDENTITY",
        "BOOTSTRAP_PASSWORD_SOCIO",
        "REDIS_EDGE_PASSWORD",
        "REDIS_DATA_PASSWORD",
    ):
        assert key in env.SECRET_KEYS


def test_redis_url_follows_generated_data_password() -> None:
    template = (
        "REDIS_URL=redis://data:local-redis-data@redis:6379/0\n"
        "REDIS_EDGE_PASSWORD=\nREDIS_DATA_PASSWORD=\n"
    )
    text = env.fill_secrets(template, _generator())
    values = {k: v for k, _, v in (line.partition("=") for line in text.splitlines())}
    assert values["REDIS_DATA_PASSWORD"] == "gen2"
    assert values["REDIS_URL"] == "redis://data:gen2@redis:6379/0"


def test_existing_env_keeps_values_for_new_keys(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    target = tmp_path / ".env"
    template.write_text("REDIS_DATA_PASSWORD=\n", encoding="utf-8")
    target.write_text("REDIS_DATA_PASSWORD=mine\n", encoding="utf-8")
    assert env.ensure_env(template, target, _generator()) is False
    assert target.read_text(encoding="utf-8") == "REDIS_DATA_PASSWORD=mine\n"
