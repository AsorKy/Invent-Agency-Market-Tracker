"""Verify the lens contract and the five-question checker (REQ-LNS-001, REQ-LNS-002)."""

import importlib.util
from pathlib import Path

import pytest

from libs.contracts.lens import Lens, is_complete, missing_metrics, required_metrics

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_five_questions", ROOT / "ops/check_five_questions.py"
)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

FULL = """# Feature
## D: metric?
Daily count, fresh within 26h.
## Dx: diagnosis?
Drift report.
## P: prediction?
Score with calibration.
## Prescriptive: gate?
Funnel gate G2.
## V: project metric?
Win rate, kill below 10 percent.
"""


def test_lens_values():
    assert [x.value for x in Lens] == ["D", "Dx", "P", "prescriptive", "V"]


def test_required_metrics():
    assert required_metrics("D") == ("freshness", "completeness")
    assert required_metrics(Lens.PREDICTIVE) == ("auc", "calibration", "lift")
    assert len(required_metrics(Lens.DIAGNOSTIC)) == 5
    with pytest.raises(ValueError):
        required_metrics("X")


def test_missing_metrics():
    assert missing_metrics("D", {"freshness": 1}) == ["completeness"]
    assert missing_metrics("D", {"freshness": 1, "completeness": None}) == ["completeness"]
    assert is_complete("P", {"auc": 0.7, "calibration": 0.1, "lift": 2.0})
    assert not is_complete("P", {})


def test_checker_pass_and_fail(tmp_path):
    (tmp_path / "_TEMPLATE.md").write_text("## D\n", encoding="utf-8")
    assert checker.check_directory(tmp_path) == []
    (tmp_path / "ok.md").write_text(FULL, encoding="utf-8")
    assert checker.check_directory(tmp_path) == []
    (tmp_path / "bad.md").write_text(FULL.replace("Drift report.\n", ""), encoding="utf-8")
    assert checker.check_directory(tmp_path) == ["bad.md: missing answers for Dx"]


def test_checker_missing_heading_and_missing_dir(tmp_path):
    assert checker.missing_answers(FULL.split("## V")[0]) == ["V"]
    assert checker.missing_answers("") == list(checker.QUESTIONS)
    assert checker.check_directory(tmp_path / "none") == []


def test_template_fails_when_copied_empty():
    text = (ROOT / "docs/features/_TEMPLATE.md").read_text(encoding="utf-8")
    assert checker.missing_answers(text) == list(checker.QUESTIONS)
    assert checker.main() == 0
