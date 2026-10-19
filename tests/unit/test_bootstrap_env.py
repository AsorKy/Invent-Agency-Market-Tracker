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
