"""Funnel engine tests: hand-computed verdicts, example refusal, event emission (REQ-LNS-009)."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from libs.domain.funnel import (
    GATES,
    Checklist,
    ChecklistError,
    ExampleChecklistRefusedError,
    Verdict,
    evaluate_gate,
    is_production,
    load_checklist,
)
from services.core.app.funnel import ProjectNotFoundError, evaluate_project_gate

LOCAL = {"APP_PROFILE": "local", "ENVIRONMENT": "local"}
PROD = {"APP_PROFILE": "production", "ENVIRONMENT": "production"}


def checklist(max_pivots: int | None = None) -> Checklist:
    return Checklist.model_validate(
        {
            "version": 7,
            "example": False,
            "gate": "G3",
            "name": "t",
            "items": [
                {"id": "k", "op": "eq", "threshold": 0, "type": "kill"},
                {"id": "r", "op": "gt", "threshold": 0, "type": "required"},
                {"id": "s1", "op": "gte", "threshold": 3, "type": "score", "weight": 3},
                {"id": "s2", "op": "lte", "threshold": 18, "type": "score", "weight": 1},
            ],
            "rules": {"advance_min_score": 0.7, "max_pivots": max_pivots},
        }
    )


def verdict(inputs: dict, **kw) -> Verdict:
    return evaluate_gate(checklist(**kw), inputs).verdict


def test_advance_when_all_pass() -> None:
    d = evaluate_gate(checklist(), {"k": 0, "r": 1, "s1": 4, "s2": 10})
    assert d.verdict is Verdict.ADVANCE and d.score == 1.0 and d.checklist_version == 7


def test_advance_at_exact_threshold_score() -> None:
    # s1 (weight 3) passes, s2 (weight 1) fails: 3/4 = 0.75 >= 0.7
    d = evaluate_gate(checklist(), {"k": 0, "r": 1, "s1": 3, "s2": 99})
    assert d.verdict is Verdict.ADVANCE and d.score == 0.75


def test_kill_beats_missing_and_failed() -> None:
    assert verdict({"k": 1}) is Verdict.KILL
    d = evaluate_gate(checklist(), {"k": 1, "r": -1})
    assert d.kill_triggers == ["k"]


def test_wait_on_missing_required_or_kill_data() -> None:
    assert verdict({"k": 0, "s1": 4, "s2": 1}) is Verdict.WAIT
    assert verdict({"r": 1, "s1": 4, "s2": 1}) is Verdict.WAIT
    assert verdict({"k": 0, "r": None}) is Verdict.WAIT


def test_pivot_on_failed_required() -> None:
    assert verdict({"k": 0, "r": 0, "s1": 4, "s2": 1}) is Verdict.PIVOT


def test_score_below_threshold_pivot_or_wait() -> None:
    # s1 fails (3), s2 passes (1): 0.25 < 0.7, no missing data -> PIVOT
    assert verdict({"k": 0, "r": 1, "s1": 1, "s2": 5}) is Verdict.PIVOT
    # s2 missing: 3/4 passes would be ADVANCE; here s1 missing -> score 0.25, missing -> WAIT
    assert verdict({"k": 0, "r": 1, "s2": 5}) is Verdict.WAIT


def test_pivot_limit_turns_pivot_into_kill() -> None:
    base = {"k": 0, "r": 0, "s1": 4, "s2": 1}
    assert verdict({**base, "pivot_count": 1}, max_pivots=2) is Verdict.PIVOT
    d = evaluate_gate(checklist(max_pivots=2), {**base, "pivot_count": 2})
    assert d.verdict is Verdict.KILL and d.kill_triggers == ["max_pivots"]


def test_decision_is_deterministic_and_keeps_inputs() -> None:
    inputs = {"k": 0, "r": 1, "s1": 4, "s2": 10}
    assert evaluate_gate(checklist(), inputs) == evaluate_gate(checklist(), inputs)
    assert evaluate_gate(checklist(), inputs).inputs == inputs


@pytest.mark.parametrize("gate", GATES)
def test_shipped_examples_load_locally_and_run(gate: str) -> None:
    loaded = load_checklist(gate, env=LOCAL)
    assert loaded.example is True and loaded.version >= 1 and loaded.gate == gate
    assert evaluate_gate(loaded, {}).verdict is Verdict.WAIT


@pytest.mark.parametrize("gate", GATES)
def test_examples_refused_in_production(gate: str) -> None:
    with pytest.raises(ExampleChecklistRefusedError):
        load_checklist(gate, env=PROD)
    assert load_checklist(gate, env={**PROD, "FUNNEL_ALLOW_EXAMPLE_CHECKLISTS": "true"})


def test_environment_convention() -> None:
    assert not is_production({})
    assert not is_production({"ENVIRONMENT": "ci"})
    assert is_production({"ENVIRONMENT": "staging"})
    assert is_production({"APP_PROFILE": "production"})


def test_non_example_loads_in_production_and_errors(tmp_path: Path) -> None:
    (tmp_path / "g1_x.yaml").write_text(
        "version: 2\nexample: false\ngate: G1\nname: x\n"
        "items:\n  - {id: a, op: gte, threshold: 1, type: score}\n",
        encoding="utf-8",
    )
    assert load_checklist("G1", directory=tmp_path, env=PROD).version == 2
    with pytest.raises(ChecklistError):
        load_checklist("G2", directory=tmp_path, env=PROD)
    with pytest.raises(ChecklistError):
        load_checklist("G9", directory=tmp_path, env=PROD)


class FakeTx:
    def __init__(self, project: dict | None) -> None:
        self.project = project
        self.audits: list[dict] = []
        self.events: list[dict] = []

    async def get_project(self, project_id, tenant_id):
        return self.project

    async def add_audit(self, **kw) -> None:
        self.audits.append(kw)

    async def add_outbox_event(self, **kw) -> None:
        self.events.append(kw)


class FakeUow:
    def __init__(self, tx: FakeTx) -> None:
        self.tx = tx

    async def __aenter__(self) -> FakeTx:
        return self.tx

    async def __aexit__(self, *exc) -> None:
        return None


def run(inputs: dict, project: dict | None = None, **kw) -> FakeTx:
    tenant = uuid4()
    tx = FakeTx(project if project is not None else {"tenant_id": tenant})
    asyncio.run(evaluate_project_gate(FakeUow(tx), uuid4(), "G3", inputs, checklist=checklist(), **kw))
    return tx


def types(tx: FakeTx) -> list[str]:
    return [e["event_type"] for e in tx.events]


def test_events_advance_and_wait_emit_only_decision() -> None:
    assert types(run({"k": 0, "r": 1, "s1": 4, "s2": 1})) == ["funnel.gate_decision"]
    assert types(run({})) == ["funnel.gate_decision"]


def test_events_pivot() -> None:
    tx = run({"k": 0, "r": 0, "s1": 4, "s2": 1})
    assert types(tx) == ["funnel.gate_decision", "funnel.pivot_requested"]


def test_events_kill_and_audit_row() -> None:
    tx = run({"k": 1}, request_id="req-1")
    assert types(tx) == [
        "funnel.gate_decision",
        "funnel.project_killed",
        "funnel.kill_trigger_event",
    ]
    audit = tx.audits[0]
    assert audit["action"] == "funnel.gate_decision"
    assert audit["payload"]["inputs"] == {"k": 1}
    assert audit["payload"]["checklist_version"] == 7
    assert audit["payload"]["verdict"] == "KILL"
    assert tx.events[0]["payload"]["request_id"] == "req-1"
    assert tx.events[0]["tenant_id"] == tx.project["tenant_id"]


def test_unknown_project() -> None:
    tx = FakeTx(None)
    with pytest.raises(ProjectNotFoundError):
        asyncio.run(
            evaluate_project_gate(FakeUow(tx), uuid4(), "G3", {}, checklist=checklist())
        )
    assert tx.events == [] and tx.audits == []
