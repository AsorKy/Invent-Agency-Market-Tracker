"""Relay wiring tests: one relay, started by compose and procs.toml (D-17, REQ-EVT-006)."""

import asyncio
import importlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _modules(command: list[str]) -> list[str]:
    return [command[i + 1] for i, part in enumerate(command) if part == "-m"]


def _relay_modules() -> list[str]:
    found: list[str] = []
    procs = tomllib.loads((ROOT / "services/core/procs.toml").read_text())
    for proc in procs["proc"]:
        if "relay" in proc["name"]:
            found += _modules(proc["cmd"])
    compose = (ROOT / "docker-compose.yml").read_text()
    for match in re.finditer(r"^\s*command:\s*(\[.*\])\s*$", compose, re.MULTILINE):
        if "relay" in match.group(1):
            found += _modules(re.findall(r'"([^"]+)"', match.group(1)))
    return found


def test_relay_entries_name_modules_that_expose_main() -> None:
    modules = _relay_modules()
    assert len(modules) >= 2
    for name in modules:
        assert callable(getattr(importlib.import_module(name), "main", None)), name


def test_outbox_relay_main_delegates_to_dead_letter_loop(monkeypatch) -> None:
    from services.core.app import dead_letter, outbox_relay

    calls: list[str] = []

    async def fake_main() -> None:
        calls.append("dead_letter")

    monkeypatch.setattr(dead_letter, "main", fake_main)
    asyncio.run(outbox_relay.main())
    assert calls == ["dead_letter"]
