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


def test_chain_events_after_is_empty_when_flag_off() -> None:
    from libs.domain.event_chain import chain_events_after

    parent = {"event_id": str(uuid4()), "event_type": "signal.created", "payload": {}}
    steps = [("signal.tagged", {"a": 1}, "tag-v1")]
    assert chain_events_after(parent, steps, enabled=False) == []


def test_chain_events_after_links_and_bounds_source_version() -> None:
    from libs.domain.event_chain import chain_events_after

    root = stamp_payload({}, "signal.created", "core-v1")
    parent = {"event_id": str(uuid4()), "event_type": "signal.created", "payload": root}
    steps = [(kind, {}, "p-v1") for kind in CHAIN[1:]]
    events = chain_events_after(parent, steps, enabled=True)
    assert [e["event_type"] for e in events] == list(CHAIN[1:])
    assert events[0]["payload"]["parent"]["event_id"] == parent["event_id"]
    assert events[-1]["payload"]["source_version"].count("<") == len(CHAIN) - 1
    assert trace_chain([parent, *events]).complete


def test_emit_flag_defaults_off() -> None:
    pytest.importorskip("pydantic_settings")
    from libs.common.settings import Settings

    assert Settings().event_chain_emit is False
