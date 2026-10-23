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


def _chain_samples():
    a, b, c = str(uuid4()), str(uuid4()), str(uuid4())
    return {
        "signal.tagged": {
            "signal_id": a, "company_id": b,
            "tags": {"source": "rues", "signal_type": "new_company"}, "parent_event_id": c,
        },
        "signal.scored": {
            "signal_id": a, "company_id": b, "score": 80, "score_version": "v1",
            "parent_event_id": None,
        },
        "company.matched": {
            "company_id": b, "lead_id": a, "rues_matched": True, "parent_event_id": c,
        },
        "signal.explained": {
            "lead_id": a,
            "company_id": b,
            "qualification_reason": {"fit": "high"},
            "parent_event_id": c,
        },
        "project.pipeline_updated": {
            "lead_id": a, "project_id": None, "from": "new", "to": "qualified", "version": 2,
            "parent_event_id": c,
        },
        "project.forecast_updated": {
            "lead_id": a, "project_id": None, "stage": "qualified", "parent_event_id": c,
        },
    }  # fmt: skip


def test_chain_registry_covers_the_six_reserved_chain_names():
    pytest.importorskip("pydantic")
    from libs.contracts.events import CHAIN_PAYLOAD_MODELS, ReservedEventType

    assert set(CHAIN_PAYLOAD_MODELS) == set(_chain_samples())
    assert set(CHAIN_PAYLOAD_MODELS) <= {e.value for e in ReservedEventType}


@pytest.mark.parametrize("event_type", sorted(_chain_samples()))
def test_chain_payload_valid_missing_and_extra(event_type):
    pytest.importorskip("pydantic")
    from pydantic import ValidationError

    from libs.contracts.events import validate_chain_payload

    sample = _chain_samples()[event_type]
    model = validate_chain_payload(event_type, sample)
    assert model.schema_version == 1
    with pytest.raises(ValidationError):
        validate_chain_payload(event_type, sample | {"unexpected": 1})
    required = "company_id" if event_type != "project.pipeline_updated" else "to"
    required = "lead_id" if event_type.startswith("project.") else required
    with pytest.raises(ValidationError):
        validate_chain_payload(event_type, {k: v for k, v in sample.items() if k != required})


def test_parent_event_id_is_mandatory_but_derived_from_the_parent_block():
    pytest.importorskip("pydantic")
    from pydantic import ValidationError

    from libs.contracts.events import CHAIN_PAYLOAD_MODELS, validate_chain_payload

    sample = _chain_samples()["project.forecast_updated"]
    bare = {k: v for k, v in sample.items() if k != "parent_event_id"}
    with pytest.raises(ValidationError):
        CHAIN_PAYLOAD_MODELS["project.forecast_updated"].model_validate(bare)
    assert validate_chain_payload("project.forecast_updated", bare).parent_event_id is None
    parent_id = str(uuid4())
    stamped = bare | {"parent": {"event_id": parent_id, "event_type": "x", "source_version": "v"}}
    assert str(validate_chain_payload("project.forecast_updated", stamped).parent_event_id) == (
        parent_id
    )


async def test_every_t159_producer_payload_validates(monkeypatch, fake_redis):
    pytest.importorskip("pydantic")
    pytest.importorskip("pydantic_settings")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("structlog")
    pytest.importorskip("redis.asyncio")
    pytest.importorskip("fakeredis")
    import json

    from libs.contracts.events import CHAIN_PAYLOAD_MODELS, validate_chain_payload
    from services.ml.app.scoring_service import STREAM_NAME
    from tests.unit.test_event_chain_producers import Flow, set_flag

    set_flag(monkeypatch, True)
    flow = Flow(fake_redis)
    await flow.run()
    payloads = [(r["event_type"], r["payload"]) for r in flow.engine.outbox]
    for _id, fields in await fake_redis.xrange(STREAM_NAME):
        payloads.append((fields["event_type"], json.loads(fields["payload"])))
    seen = set()
    for event_type, payload in payloads:
        if event_type in CHAIN_PAYLOAD_MODELS:
            validate_chain_payload(event_type, payload)
            seen.add(event_type)
    assert seen == set(CHAIN_PAYLOAD_MODELS)
