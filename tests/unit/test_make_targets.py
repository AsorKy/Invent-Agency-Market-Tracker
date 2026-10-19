"""Check the Makefile run targets without Docker (REQ-RUN-001)."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
TARGETS = [
    "invent-tracker",
    "invent-tracker-down",
    "invent-tracker-reset",
    "seed",
    "seed-clear",
    "openapi",
    "test-integration",
]


def _defined() -> set[str]:
    return set(re.findall(r"^([A-Za-z0-9_-]+):", MAKEFILE.read_text(encoding="utf-8"), re.M))


def _phony() -> set[str]:
    text = MAKEFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    match = re.search(r"^\.PHONY:(.*)$", text, re.M)
    assert match
    return set(match.group(1).split())


@pytest.mark.parametrize("target", TARGETS)
def test_target_defined_and_phony(target: str) -> None:
    assert target in _defined()
    assert target in _phony()


def _dry_run(target: str) -> str:
    result = subprocess.run(
        ["make", "-n", target], cwd=ROOT, capture_output=True, text=True, check=True, timeout=60
    )
    return result.stdout


def test_invent_tracker_steps_in_order() -> None:
    out = _dry_run("invent-tracker")
    steps = [
        "ops/bootstrap_env.py",
        "up -d --wait postgres redis",
        "ops/bootstrap_roles.py",
        "up -d --build",
        "ops/bootstrap_env.py --wait",
        ".env.seeded",
    ]
    positions = [out.index(step) for step in steps]
    assert positions == sorted(positions)


def test_seed_runs_inside_the_stack() -> None:
    assert "ops.seed load" in _dry_run("seed")
    assert "ops.seed clear" in _dry_run("seed-clear")


def test_down_and_reset() -> None:
    assert "docker compose down" in _dry_run("invent-tracker-down")
    reset = _dry_run("invent-tracker-reset")
    assert "read answer" in reset
    assert "down -v" in reset
