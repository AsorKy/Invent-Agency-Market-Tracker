"""Unit tests for the SPEC.md and TASKS.md traceability guard."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "check_traceability.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_traceability", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


def requirement(
    identifier: str, status: str = "accepted", layer: str = "L1", scope: str = "run1"
) -> str:
    head = f"### {identifier} Title\n"
    return head + f"- status: {status}\n- layer: {layer}\n- scope: {scope}\n- source: ADR\n\n"


def task(identifier: str, spec: str) -> str:
    return f"### {identifier} Title\n- status: todo\n- spec: {spec}\n- owner: qa-reviewer\n\n"


def test_passes_when_every_citation_is_known_and_covered() -> None:
    spec = requirement("REQ-QA-001") + requirement("REQ-QA-002")
    tasks = task("T-001", "REQ-QA-001, REQ-QA-002")
    assert MODULE.check(spec, tasks) == ([], [])


def test_unknown_requirement_id_is_an_error() -> None:
    errors, _ = MODULE.check(requirement("REQ-QA-001"), task("T-001", "REQ-QA-001, REQ-QA-999"))
    assert errors == ["T-001: unknown requirement REQ-QA-999"]


def test_uncovered_accepted_l1_run1_requirement_is_an_error() -> None:
    spec = requirement("REQ-QA-001") + requirement("REQ-QA-002")
    errors, _ = MODULE.check(spec, task("T-001", "REQ-QA-001"))
    assert len(errors) == 1 and "REQ-QA-002" in errors[0]


def test_non_accepted_and_deferred_requirements_are_not_required() -> None:
    spec = (
        requirement("REQ-QA-001")
        + requirement("REQ-QA-002", status="proposed")
        + requirement("REQ-QA-003", status="deprecated")
        + requirement("REQ-QA-004", scope="deferred")
    )
    assert MODULE.check(spec, task("T-001", "REQ-QA-001")) == ([], [])


def test_uncovered_later_run_requirement_is_only_a_note() -> None:
    spec = requirement("REQ-QA-001") + requirement("REQ-LNS-001", layer="L2", scope="later-run")
    errors, notes = MODULE.check(spec, task("T-001", "REQ-QA-001"))
    assert errors == []
    assert len(notes) == 1 and "REQ-LNS-001" in notes[0]


def test_format_templates_in_code_fences_are_ignored() -> None:
    spec = "```text\n### REQ-<AREA>-<NNN> Title\n- status: accepted\n```\n" + requirement(
        "REQ-QA-001"
    )
    tasks = "```text\n### T-NNN Title\n- spec: REQ-AREA-NNN\n```\n" + task("T-001", "REQ-QA-001")
    assert MODULE.check(spec, tasks) == ([], [])


def test_task_without_requirement_is_an_error() -> None:
    errors, _ = MODULE.check(requirement("REQ-QA-001"), task("T-001", "none"))
    assert "T-001: cites no requirement" in errors


def test_real_repository_files_have_no_errors() -> None:
    spec_text = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    tasks_text = (ROOT / "TASKS.md").read_text(encoding="utf-8")
    errors, _ = MODULE.check(spec_text, tasks_text)
    assert errors == []


def test_script_exits_zero_on_the_real_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "Traceability guard: OK" in result.stdout
