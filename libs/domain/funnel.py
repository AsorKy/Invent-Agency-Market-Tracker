"""Deterministic funnel evaluator for gates G1 to G7 (REQ-LNS-009).

Pure functions, no I/O except the YAML loader. A checklist (``config/funnel/*.yaml``) has a
``version``, an ``example`` flag, ordered ``items`` and ``rules``. ``evaluate_gate`` maps the
inputs onto one verdict, in this fixed order:

1. A ``kill`` item with data that fails its condition gives KILL.
2. A ``kill`` or ``required`` item without data (key absent or None) gives WAIT.
3. A ``required`` item that fails gives PIVOT.
4. ``score`` items: the weighted share of passed items (missing counts as not passed) must reach
   ``rules.advance_min_score`` to ADVANCE; otherwise WAIT if some score item lacks data, else PIVOT.
5. A PIVOT verdict becomes KILL when ``inputs["pivot_count"]`` reaches ``rules.max_pivots``.

Cost is O(items). Example checklists (decision D-06) are refused in production unless
``FUNNEL_ALLOW_EXAMPLE_CHECKLISTS=true``. ``services/core/app/funnel.py`` persists and emits.
"""

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

GATES = ("G1", "G2", "G3", "G4", "G5", "G6", "G7")
CHECKLIST_DIR = Path(__file__).resolve().parents[2] / "config" / "funnel"
LOCAL_ENVIRONMENTS = frozenset({"local", "test", "ci", "dev", "development"})
EXAMPLES_ENV = "FUNNEL_ALLOW_EXAMPLE_CHECKLISTS"
CHECKLIST_DIR_ENV = "FUNNEL_CHECKLIST_DIR"


class Verdict(StrEnum):
    ADVANCE = "ADVANCE"
    PIVOT = "PIVOT"
    KILL = "KILL"
    WAIT = "WAIT"


class ChecklistError(Exception):
    """A checklist is missing, malformed, or not allowed in this environment."""


class ExampleChecklistRefusedError(ChecklistError):
    """An ``example: true`` checklist was requested in production."""


class ChecklistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = ""
    op: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    threshold: float
    type: Literal["kill", "required", "score"]
    weight: float = Field(default=1.0, gt=0)


class ChecklistRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advance_min_score: float = Field(default=0.5, ge=0, le=1)
    max_pivots: int | None = Field(default=None, ge=1)


class Checklist(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    example: bool = False
    gate: Literal["G1", "G2", "G3", "G4", "G5", "G6", "G7"]
    name: str
    items: list[ChecklistItem] = Field(min_length=1)
    rules: ChecklistRules = Field(default_factory=ChecklistRules)


class ItemResult(BaseModel):
    id: str
    type: str
    status: Literal["pass", "fail", "missing"]
    value: float | None = None


class GateDecision(BaseModel):
    """Verdict plus everything needed to audit it."""

    gate: str
    verdict: Verdict
    checklist_version: int
    example_checklist: bool
    score: float | None
    reasons: list[str]
    kill_triggers: list[str]
    items: list[ItemResult]
    inputs: dict[str, Any]


def is_production(env: Mapping[str, str] | None = None) -> bool:
    """Same convention as ``ops/bootstrap_roles``: local only if profile and environment are."""
    source = os.environ if env is None else env
    profile = source.get("APP_PROFILE", "local").strip().lower()
    environment = source.get("ENVIRONMENT", "local").strip().lower()
    return not (profile == "local" and environment in LOCAL_ENVIRONMENTS)


def load_checklist(
    gate: str, *, directory: Path | None = None, env: Mapping[str, str] | None = None
) -> Checklist:
    """Load the checklist of ``gate``; refuse examples in production without the opt-in."""
    source = os.environ if env is None else env
    if gate not in GATES:
        raise ChecklistError(f"unknown gate {gate}")
    base = directory or Path(source.get(CHECKLIST_DIR_ENV) or CHECKLIST_DIR)
    matches = sorted(base.glob(f"{gate.lower()}_*.yaml"))
    if len(matches) != 1:
        raise ChecklistError(f"expected exactly one checklist for {gate} in {base}")
    with matches[0].open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    try:
        checklist = Checklist.model_validate(data)
    except ValueError as error:
        raise ChecklistError(f"invalid checklist {matches[0].name}: {error}") from error
    if checklist.gate != gate:
        raise ChecklistError(f"{matches[0].name} declares gate {checklist.gate}")
    allowed = source.get(EXAMPLES_ENV, "").strip().lower() == "true"
    if checklist.example and is_production(source) and not allowed:
        raise ExampleChecklistRefusedError(
            f"{gate} checklist is an example; set {EXAMPLES_ENV}=true or replace it"
        )
    return checklist


def _passes(op: str, value: float, threshold: float) -> bool:
    return {
        "gt": value > threshold,
        "gte": value >= threshold,
        "lt": value < threshold,
        "lte": value <= threshold,
        "eq": value == threshold,
        "ne": value != threshold,
    }[op]


def _value(inputs: Mapping[str, Any], key: str) -> float | None:
    raw = inputs.get(key)
    if raw is None or isinstance(raw, str):
        return None
    return float(raw)


def evaluate_gate(checklist: Checklist, inputs: Mapping[str, Any]) -> GateDecision:
    """Evaluate ``inputs`` against ``checklist``; deterministic and side-effect free."""
    results: list[ItemResult] = []
    kill_triggers: list[str] = []
    missing_required: list[str] = []
    failed_required: list[str] = []
    missing_score: list[str] = []
    passed_weight = 0.0
    total_weight = 0.0
    for item in checklist.items:
        value = _value(inputs, item.id)
        status: Literal["pass", "fail", "missing"]
        if value is None:
            status = "missing"
        else:
            status = "pass" if _passes(item.op, value, item.threshold) else "fail"
        results.append(ItemResult(id=item.id, type=item.type, status=status, value=value))
        if item.type == "kill":
            if status == "fail":
                kill_triggers.append(item.id)
            elif status == "missing":
                missing_required.append(item.id)
        elif item.type == "required":
            if status == "fail":
                failed_required.append(item.id)
            elif status == "missing":
                missing_required.append(item.id)
        else:
            total_weight += item.weight
            if status == "pass":
                passed_weight += item.weight
            elif status == "missing":
                missing_score.append(item.id)
    score = passed_weight / total_weight if total_weight else None

    verdict: Verdict
    reasons: list[str] = []
    if kill_triggers:
        verdict = Verdict.KILL
        reasons = [f"kill item failed: {i}" for i in kill_triggers]
    elif missing_required:
        verdict = Verdict.WAIT
        reasons = [f"missing data: {i}" for i in missing_required]
    elif failed_required:
        verdict = Verdict.PIVOT
        reasons = [f"required item failed: {i}" for i in failed_required]
    elif score is not None and score < checklist.rules.advance_min_score:
        verdict = Verdict.WAIT if missing_score else Verdict.PIVOT
        reasons = [f"score {score:.4f} below {checklist.rules.advance_min_score}"]
        reasons += [f"missing data: {i}" for i in missing_score]
    else:
        verdict = Verdict.ADVANCE
        reasons = ["all gate conditions met"]

    max_pivots = checklist.rules.max_pivots
    pivots = _value(inputs, "pivot_count")
    if verdict is Verdict.PIVOT and max_pivots is not None and (pivots or 0) >= max_pivots:
        verdict = Verdict.KILL
        kill_triggers = ["max_pivots"]
        reasons.append(f"pivot limit {max_pivots} reached")

    return GateDecision(
        gate=checklist.gate,
        verdict=verdict,
        checklist_version=checklist.version,
        example_checklist=checklist.example,
        score=score,
        reasons=reasons,
        kill_triggers=kill_triggers,
        items=results,
        inputs=dict(inputs),
    )
