"""Implement pure rules for commercial pipeline stages.

The enum and ``ALLOWED_TRANSITIONS`` describe the
new-contacted-meeting-proposal-won/lost flow; ``can_transition`` applies the
rule without a database or framework. It relates to ``core.lead`` and the
``/internal/core/pipeline/kanban`` endpoint in ``services/core/app/main.py``.
It centralizes a rule that the API and future frontend can share.
"""

from enum import StrEnum


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
    PipelineStage.DISCOVERED: frozenset({PipelineStage.QUALIFIED, PipelineStage.LOST}),
    PipelineStage.QUALIFIED: frozenset({PipelineStage.HOT, PipelineStage.LOST}),
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
