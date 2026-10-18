"""Ownership registry covers every service directory (REQ-ARC-002)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    "identity": "lead-backend-engineer",
    "brief": "lead-backend-engineer",
    "pulse": "second-backend-engineer",
    "streamlit": "second-backend-engineer",
    "mocks": "qa-reviewer",
}


def _registry() -> dict:
    return yaml.safe_load((ROOT / ".codex" / "agent-registry.yaml").read_text(encoding="utf-8"))


def _service_dirs() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "services"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    parts = (line.split("/") for line in out.splitlines() if line)
    return {p[1] for p in parts if len(p) > 2}


def _owners() -> dict[str, str]:
    owners: dict[str, str] = {}
    for agent in _registry()["agents"]:
        for path in agent["primary_paths"]:
            segs = path.strip("/").split("/")
            if len(segs) == 2 and segs[0] == "services" and segs[1] != "*":
                owners[segs[1]] = agent["id"]
    return owners


def test_every_service_dir_has_owner() -> None:
    owners = _owners()
    legacy = {Path(e["path"]).name for e in _registry().get("legacy_paths", [])}
    missing = _service_dirs() - set(owners) - legacy
    assert not missing, f"services without owner: {sorted(missing)}"


def test_required_owners() -> None:
    owners = _owners()
    for service, role in EXPECTED.items():
        assert owners.get(service) == role


def test_analytics_not_owned() -> None:
    assert "analytics" not in _owners()


def test_legacy_entries_map_to_pulse_owner() -> None:
    for entry in _registry().get("legacy_paths", []):
        assert entry["merged_into"] == "services/pulse/"
        assert _owners()["pulse"] == entry["owner"]


def test_profiles_exist_and_match_registry() -> None:
    for agent in _registry()["agents"]:
        profile = ROOT / agent["profile"]
        assert profile.is_file()
        text = profile.read_text(encoding="utf-8")
        for path in agent["primary_paths"]:
            if path.startswith("services/") and "*" not in path:
                assert f"`{path}`" in text, f"{agent['id']} profile lacks {path}"
