"""Block feature specs that leave any of the five lens questions unanswered.

The script reads every Markdown file in ``docs/features`` except those whose name
starts with an underscore (the template) and requires a non-empty answer under each of
the five headings D, Dx, P, Prescriptive, and V (ADR-IMT-0002 section 2.9, REQ-LNS-002).
It relates to ``docs/features/_TEMPLATE.md`` and ``Makefile``. It exits 0 when there are
no specs and 1 when any answer is missing or empty.
"""

import re
import sys
from pathlib import Path

QUESTIONS = ("D", "Dx", "P", "Prescriptive", "V")
HEADING = re.compile(r"^##\s+(D|Dx|P|Prescriptive|V)\b\s*:?", re.I | re.M)
COMMENT = re.compile(r"<!--.*?-->", re.S)


def missing_answers(text: str) -> list[str]:
    """Return the questions with no heading or an empty answer in a spec text."""
    matches = list(HEADING.finditer(text))
    answers: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = COMMENT.sub("", text[match.end() : end])
        body = re.sub(r"^.*\n", "", body, count=1) if "\n" in body else ""
        answers.setdefault(_canonical(match.group(1)), body.strip())
    return [q for q in QUESTIONS if not answers.get(q)]


def _canonical(label: str) -> str:
    for question in QUESTIONS:
        if question.lower() == label.lower():
            return question
    return label


def check_directory(features: Path) -> list[str]:
    """Return one failure message per incomplete spec under ``features``."""
    failures: list[str] = []
    if not features.is_dir():
        return failures
    for path in sorted(features.glob("*.md")):
        if path.name.startswith("_"):
            continue
        gaps = missing_answers(path.read_text(encoding="utf-8"))
        if gaps:
            failures.append(f"{path.name}: missing answers for {', '.join(gaps)}")
    return failures


def main() -> int:
    features = Path(__file__).resolve().parents[1] / "docs" / "features"
    failures = check_directory(features)
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
