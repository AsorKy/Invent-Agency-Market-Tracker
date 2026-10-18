"""Unit tests for the event catalog and the tolerant dispatch helper.

Covers REQ-EVT-005 (L1 catalog and envelope) and REQ-EXT-001 (reserved names,
unknown types acknowledged with a single log line). Dispatch tests use only the
standard library; envelope tests skip when pydantic is missing.
"""

import asyncio
import logging
from uuid import uuid4

import pytest

from libs.common.events_dispatch import dispatch_event

RESERVED = {
    "signal.tagged", "signal.scored", "company.matched", "signal.explained",
    "funnel.gate_decision", "funnel.pivot_requested", "funnel.project_killed",
    "funnel.kill_trigger_event", "project.pipeline_updated",
    "project.forecast_updated", "review.candidate", "component.evaluated",
}  # fmt: skip
L1 = {
    "signal.created", "company.updated", "lead.created", "lead.outcome_recorded",
    "lead.score_updated", "ingest.quarantine", "tenant.config_updated",
    "signal.activity_logged", "signal.converted_to_project",
}  # fmt: skip


def _run(fields, handlers, caplog):
    acked: list[int] = []

    async def ack() -> None:
        acked.append(1)

    with caplog.at_level(logging.INFO):
        ran = asyncio.run(dispatch_event(fields, handlers, ack))
    return ran, acked


def test_unknown_type_acked_and_one_log_line_without_payload(caplog):
    fields = {"event_type": "weird.thing", "event_id": "e1", "payload": "SECRET-BODY"}
    ran, acked = _run(fields, {}, caplog)
    assert ran is False and acked == [1]
    assert len(caplog.records) == 1
    assert "weird.thing" in caplog.text and "SECRET-BODY" not in caplog.text


def test_reserved_type_without_handler_is_tolerated(caplog):
    ran, acked = _run({"event_type": "signal.tagged"}, {}, caplog)
    assert ran is False and acked == [1] and len(caplog.records) == 1


def test_known_handler_runs_then_acks(caplog):
    seen: list[str] = []

    async def handler(fields) -> None:
        seen.append(fields["event_type"])

    ran, acked = _run({"event_type": "lead.created"}, {"lead.created": handler}, caplog)
    assert ran is True and seen == ["lead.created"] and acked == [1]
    assert not caplog.records


def test_handler_error_does_not_ack(caplog):
    async def handler(fields) -> None:
        raise RuntimeError("boom")

    acked: list[int] = []

    async def ack() -> None:
        acked.append(1)

    with pytest.raises(RuntimeError):
        asyncio.run(dispatch_event({"event_type": "x"}, {"x": handler}, ack))
    assert acked == []


def test_catalog_names_and_envelope():
    pytest.importorskip("pydantic")
    from datetime import UTC, datetime

    from libs.contracts.events import DomainEvent, EventType, ReservedEventType

    assert {e.value for e in EventType} == L1
    assert {e.value for e in ReservedEventType} == RESERVED
    assert not ({e.value for e in EventType} & RESERVED)
    ev = DomainEvent(
        event_type=EventType.SIGNAL_CREATED,
        aggregate_type="signal",
        aggregate_id=uuid4(),
        occurred_at=datetime.now(UTC),
        payload={},
    )
    assert ev.replay is False and ev.version == 1 and ev.source_version is None
    assert ev.created_at.tzinfo is not None
