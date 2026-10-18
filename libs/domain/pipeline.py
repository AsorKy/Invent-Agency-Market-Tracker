"""Implement pure rules for commercial pipeline stages.

The enum and ``ALLOWED_TRANSITIONS`` describe the
new-contacted-meeting-proposal-won/lost flow; ``can_transition`` applies the
rule without a database or framework. It relates to ``core.lead`` and the
``/internal/core/pipeline/kanban`` endpoint in ``services/core/app/main.py``.
It centralizes a rule that the API and future frontend can share. Lost is only reachable from
Hot onward (REQ-COR-002); ``new`` is the legacy database default and behaves like Hot.
``check_version`` implements the ``expected_version`` rule and ``group_by_stage`` builds the kanban
columns. Every function is O(n) or better in its input.
"""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any


class PipelineStage(StrEnum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    HOT = "hot"
    NEW = "new"
    CONTACTED = "contacted"
    MEETING = "meeting"
    PROPOSAL = "proposal"
    WON = "won"
    LOST = "lost"


ALLOWED_TRANSITIONS: dict[PipelineStage, frozenset[PipelineStage]] = {
    PipelineStage.DISCOVERED: frozenset({PipelineStage.QUALIFIED}),
    PipelineStage.QUALIFIED: frozenset({PipelineStage.HOT}),
    PipelineStage.HOT: frozenset({PipelineStage.CONTACTED, PipelineStage.LOST}),
    PipelineStage.NEW: frozenset({PipelineStage.CONTACTED, PipelineStage.LOST}),
    PipelineStage.CONTACTED: frozenset({PipelineStage.MEETING, PipelineStage.LOST}),
    PipelineStage.MEETING: frozenset({PipelineStage.PROPOSAL, PipelineStage.LOST}),
    PipelineStage.PROPOSAL: frozenset({PipelineStage.WON, PipelineStage.LOST}),
    PipelineStage.WON: frozenset(),
    PipelineStage.LOST: frozenset(),
}


def can_transition(current: PipelineStage, target: PipelineStage) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


class LeadStateError(Exception):
    """Base class for rejected lead state changes."""


class InvalidTransitionError(LeadStateError):
    def __init__(self, current: PipelineStage, target: PipelineStage) -> None:
        super().__init__(f"invalid_transition:{current}->{target}")
        self.current = current
        self.target = target
        self.allowed = allowed_targets(current)


class VersionConflictError(LeadStateError):
    def __init__(self, expected: int, current: int) -> None:
        super().__init__(f"version_conflict:expected={expected},current={current}")
        self.expected = expected
        self.current = current


def parse_stage(value: str) -> PipelineStage:
    """Return the stage for ``value`` or raise ``ValueError`` for an unknown name."""
    try:
        return PipelineStage(value)
    except ValueError as exc:
        raise ValueError(f"unknown_stage:{value}") from exc


def allowed_targets(current: PipelineStage) -> list[PipelineStage]:
    """Return the legal next stages in declaration order."""
    return [stage for stage in PipelineStage if stage in ALLOWED_TRANSITIONS[current]]


def check_version(expected: int, current: int) -> None:
    """Raise ``VersionConflictError`` when the client's version is stale."""
    if expected != current:
        raise VersionConflictError(expected, current)


def validate_transition(
    current: PipelineStage, target: PipelineStage, *, expected_version: int, version: int
) -> None:
    """Apply the version check first, then the transition rule."""
    check_version(expected_version, version)
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def group_by_stage(leads: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Return one kanban column per stage in pipeline order, including empty ones."""
    columns: dict[str, list[Mapping[str, Any]]] = {stage.value: [] for stage in PipelineStage}
    for lead in leads:
        columns.setdefault(str(lead["stage"]), []).append(lead)
    return columns
