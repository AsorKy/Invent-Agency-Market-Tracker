"""Check that every `tasks:` line of SPEC.md lists real task ids or states a reason.

Relates to REQ-TRC-001 and ops/check_traceability.py. A requirement's line is either
a comma-separated list of ids that exist as headings in TASKS.md, or `none, <reason>`.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASK_HEADING = re.compile(r"^### (T-\d{3})\b", re.MULTILINE)
REQ_HEADING = re.compile(r"^### (REQ-[A-Z]+-\d{3})\b")
TASK_ID = re.compile(r"^T-\d{3}$")


def _tasks_lines() -> list[tuple[str, str]]:
    """Return (requirement id, tasks value) pairs outside fenced code blocks."""
    pairs: list[tuple[str, str]] = []
    req = ""
    in_fence = False
    for line in (ROOT / "SPEC.md").read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = REQ_HEADING.match(line)
        if match:
            req = match.group(1)
        elif line.startswith("- tasks:") and req:
            pairs.append((req, line.split(":", 1)[1].strip()))
    return pairs


def test_every_requirement_has_a_tasks_line() -> None:
    reqs = {
        m.group(1)
        for line in (ROOT / "SPEC.md").read_text(encoding="utf-8").splitlines()
        if (m := REQ_HEADING.match(line))
    }
    assert {r for r, _ in _tasks_lines()} == reqs


def test_tasks_lines_are_existing_ids_or_a_stated_reason() -> None:
    known = set(TASK_HEADING.findall((ROOT / "TASKS.md").read_text(encoding="utf-8")))
    bad: list[str] = []
    for req, value in _tasks_lines():
        if value.startswith("none,") and len(value[5:].strip()) >= 3:
            continue
        ids = [part.strip() for part in value.split(",")]
        if not all(TASK_ID.match(i) and i in known for i in ids):
            bad.append(f"{req}: {value}")
    assert not bad, bad


def _citations() -> dict[str, set[str]]:
    """Map each requirement id to the tasks whose `spec:` line cites it (outside fences)."""
    cited: dict[str, set[str]] = {}
    task = ""
    in_fence = False
    for line in (ROOT / "TASKS.md").read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = TASK_HEADING.match(line)
        if match:
            task = match.group(1)
        elif line.startswith("#"):
            task = ""
        elif task and line.startswith("- spec:"):
            for req in re.findall(r"REQ-[A-Z]+-\d{3}", line):
                cited.setdefault(req, set()).add(task)
    return cited


def _missing(lines: dict[str, str], cited: dict[str, set[str]]) -> list[str]:
    """Return `REQ: T-NNN` entries where a citing task is absent from the requirement's line."""
    problems: list[str] = []
    for req, tasks in sorted(cited.items()):
        listed = {part.strip() for part in lines.get(req, "").split(",")}
        problems.extend(f"{req}: {task}" for task in sorted(tasks - listed))
    return problems


def test_every_citing_task_is_listed_in_the_tasks_line() -> None:
    assert _missing(dict(_tasks_lines()), _citations()) == []


def test_missing_task_in_a_tasks_line_is_detected() -> None:
    lines = dict(_tasks_lines())
    cited = {**_citations(), "REQ-TRC-001": {"T-002", "T-999"}}
    assert _missing(lines, cited) == ["REQ-TRC-001: T-999"]
