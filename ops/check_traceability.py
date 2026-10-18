"""Enforce traceability between SPEC.md requirements and TASKS.md tasks.

The checker parses the ``### REQ-...`` headings of ``SPEC.md`` with their status,
layer, and scope lines, and the ``- spec:`` lines of the ``### T-NNN`` blocks of
``TASKS.md``. It relates to the Autonomous run protocol in ``AGENTS.md`` and
complements ``ops/check_language.py``. Its purpose is to fail when a task cites an
unknown requirement or when an accepted L1 run1 requirement has no task, and to
report accepted later-run requirements that no task cites yet.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REQ_HEADING = re.compile(r"^### (REQ-[A-Z]+-\d{3})\b")
TASK_HEADING = re.compile(r"^### (T-\d{3})\b")
FIELD_LINE = re.compile(r"^- (status|layer|scope|spec):\s*(.*?)\s*$")
REQ_ID = re.compile(r"REQ-[A-Z]+-\d{3}")


@dataclass(frozen=True)
class Requirement:
    """One requirement parsed from SPEC.md."""

    identifier: str
    status: str
    layer: str
    scope: str


def _content_lines(text: str) -> list[str]:
    """Return the lines outside fenced code blocks, so format templates are ignored."""
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def parse_requirements(text: str) -> dict[str, Requirement]:
    """Parse requirement headings and their status, layer, and scope lines."""
    requirements: dict[str, Requirement] = {}
    current: str | None = None
    fields: dict[str, str] = {}

    def flush() -> None:
        if current is not None:
            requirements[current] = Requirement(
                current,
                fields.get("status", ""),
                fields.get("layer", ""),
                fields.get("scope", ""),
            )

    for line in _content_lines(text):
        heading = REQ_HEADING.match(line)
        if heading:
            flush()
            current = heading.group(1)
            fields = {}
            continue
        if line.startswith("#"):
            flush()
            current = None
            continue
        field = FIELD_LINE.match(line)
        if current is not None and field and field.group(1) != "spec":
            fields.setdefault(field.group(1), field.group(2))
    flush()
    return requirements


def parse_task_specs(text: str) -> dict[str, list[str]]:
    """Parse the requirement IDs cited by the ``- spec:`` line of each task."""
    tasks: dict[str, list[str]] = {}
    current: str | None = None
    for line in _content_lines(text):
        heading = TASK_HEADING.match(line)
        if heading:
            current = heading.group(1)
            tasks[current] = []
            continue
        if line.startswith("#"):
            current = None
            continue
        field = FIELD_LINE.match(line)
        if current is not None and field and field.group(1) == "spec":
            tasks[current].extend(REQ_ID.findall(field.group(2)))
    return tasks


def check(spec_text: str, tasks_text: str) -> tuple[list[str], list[str]]:
    """Return the blocking errors and the informational notes."""
    requirements = parse_requirements(spec_text)
    tasks = parse_task_specs(tasks_text)
    cited = {identifier for identifiers in tasks.values() for identifier in identifiers}
    errors: list[str] = []
    notes: list[str] = []
    for task, identifiers in tasks.items():
        if not identifiers:
            errors.append(f"{task}: cites no requirement")
        errors.extend(
            f"{task}: unknown requirement {identifier}"
            for identifier in identifiers
            if identifier not in requirements
        )
    for requirement in requirements.values():
        if requirement.identifier in cited or requirement.status != "accepted":
            continue
        if requirement.layer == "L1" and requirement.scope == "run1":
            errors.append(
                f"{requirement.identifier}: accepted L1 run1 requirement cited by no task"
            )
        elif requirement.scope == "later-run":
            notes.append(
                f"{requirement.identifier}: accepted later-run requirement cited by no task"
            )
    return errors, notes


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    try:
        spec_text = (repository / "SPEC.md").read_text(encoding="utf-8")
        tasks_text = (repository / "TASKS.md").read_text(encoding="utf-8")
    except OSError as error:
        print(f"Traceability guard: cannot read input ({error})", file=sys.stderr)
        return 1
    errors, notes = check(spec_text, tasks_text)
    for note in notes:
        print(f"note: {note}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    requirements = parse_requirements(spec_text)
    tasks = parse_task_specs(tasks_text)
    print(f"Traceability guard: OK ({len(requirements)} requirements, {len(tasks)} tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
