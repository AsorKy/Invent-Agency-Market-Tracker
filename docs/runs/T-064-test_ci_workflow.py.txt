"""Check the eleven-stage CI workflow: stage names, order, gating, no secrets (REQ-CI-002)."""

import re
from pathlib import Path
from typing import Any

import yaml

CI_PATH = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
STAGES = [
    "static",
    "unit",
    "integration",
    "contracts",
    "e2e",
    "images",
    "deploy-migrate",
    "deploy-backend",
    "deploy-frontend",
    "deploy-streamlit",
    "deploy-smoke",
]


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))["jobs"]
    return jobs


def test_stage_names_and_order() -> None:
    assert list(_jobs()) == STAGES


def test_each_stage_needs_the_previous_one() -> None:
    jobs = _jobs()
    for previous, current in zip(STAGES, STAGES[1:], strict=False):
        assert jobs[current]["needs"] == [previous]


def test_make_targets_are_used() -> None:
    text = CI_PATH.read_text(encoding="utf-8")
    for target in ("make static", "make test", "make test-integration", "make openapi"):
        assert target in text


def test_contracts_stage_runs_oasdiff_breaking_on_committed_document() -> None:
    steps = " ".join(str(s.get("run", "")) for s in _jobs()["contracts"]["steps"])
    assert "oasdiff" in steps and "breaking" in steps
    assert "docs/api/openapi.json" in steps


def test_deploy_stages_are_gated_on_secrets() -> None:
    jobs = _jobs()
    for name in STAGES[6:]:
        job = jobs[name]
        assert "refs/heads/main" in job["if"]
        gate = next(s for s in job["steps"] if s.get("id") == "gate")
        assert "secrets." in gate["env"]["HAS_SECRET"]
        for step in job["steps"]:
            if step.get("id") != "gate" and ("run" in step or "uses" in step):
                if step.get("uses", "").startswith("actions/checkout"):
                    continue
                assert "steps.gate.outputs.enabled" in step["if"]


def test_no_secret_values() -> None:
    text = CI_PATH.read_text(encoding="utf-8")
    pattern = r"(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{20,})"
    assert not re.search(pattern, text)
    assert "BEGIN PRIVATE KEY" not in text
