"""Test the L2 event chain source_version rule and trace (REQ-LNS-012)."""

from uuid import uuid4

import pytest

from libs.domain.event_chain import CHAIN, build_source_version, stamp_payload, trace_chain


def _build(skip: str | None = None) -> list[dict]:
    events: list[dict] = []
    parent = None
    for kind in CHAIN:
        payload = stamp_payload({"k": kind}, kind, f"{kind.split('.')[0]}-v1", parent)
        event = {"event_id": str(uuid4()), "event_type": kind, "payload": payload}
        parent = event
        if kind != skip:
            events.append(event)
    return events


def test_every_event_carries_source_version_and_parent() -> None:
    events = _build()
    assert len(events) == 8
    for event in events:
        assert event["payload"]["source_version"]
    assert "parent" not in events[0]["payload"]
    assert events[1]["payload"]["parent"]["event_id"] == events[0]["event_id"]
    assert events[-1]["payload"]["source_version"].count("<") == 7


def test_trace_complete_even_when_shuffled() -> None:
    trace = trace_chain(reversed(_build()))
    assert trace.complete and trace.path == CHAIN and trace.gap is None


def test_trace_reports_gap() -> None:
    trace = trace_chain(_build(skip="company.matched"))
    assert not trace.complete
    assert trace.gap == "company.matched"
    assert trace_chain(_build()[:3]).gap == "company.matched"
    assert trace_chain([]).gap == "signal.created"


def test_missing_source_version_is_a_problem() -> None:
    events = _build()
    del events[2]["payload"]["source_version"]
    trace = trace_chain(events)
    assert not trace.complete and "signal.scored has no source_version" in trace.problems


def test_rules_reject_bad_input() -> None:
    with pytest.raises(ValueError):
        build_source_version("")
    with pytest.raises(ValueError):
        stamp_payload({}, "signal.tagged", "v1")
    with pytest.raises(ValueError):
        stamp_payload({}, "lead.created", "v1")
