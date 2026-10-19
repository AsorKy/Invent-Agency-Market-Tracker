"""Unit tests for the ``lead.score_updated`` envelope and Pulse poison handling (T-121).

Covers REQ-EVT-005 (ML publishes the core relay envelope with ``payload`` and ``tenant_id``) and
REQ-EVT-002 (a malformed entry never blocks the Pulse worker). Redis is fakeredis and the
database is the in-memory fake of ``test_pulse_consumer.py``; no external service is used.
"""

import json
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("sqlalchemy")
pytest.importorskip("structlog")
pytest.importorskip("redis.asyncio")
pytest.importorskip("fakeredis")

from test_pulse_consumer import FakePulseDb, install_consumer, stream_message  # noqa: E402

from services.core.app.dead_letter import stream_fields  # noqa: E402
from services.ml.app.scoring_service import score_event_fields  # noqa: E402
from services.pulse.app import consumer as pulse_consumer  # noqa: E402
from support_outbox import StopLoop  # noqa: E402

TENANT = uuid4()
COMPANY = uuid4()


def test_score_event_has_the_core_relay_envelope() -> None:
    fields = score_event_fields(
        company_id=COMPANY, tenant_id=TENANT, score=80, is_hot=True, source_version="2"
    )
    relay = stream_fields(
        {
            "event_id": fields["event_id"],
            "tenant_id": TENANT,
            "event_type": "lead.score_updated",
            "aggregate_type": "company",
            "aggregate_id": COMPANY,
            "version": 1,
            "payload": json.loads(fields["payload"]),
        }
    )
    assert {k: fields[k] for k in relay} == relay
    assert fields["tenant_id"] == str(TENANT)
    assert json.loads(fields["payload"])["score"] == 80
    assert fields["score"] == "80"


def test_score_event_without_tenant_omits_the_field() -> None:
    fields = score_event_fields(
        company_id=COMPANY, tenant_id=None, score=1, is_hot=False, source_version="1"
    )
    assert "tenant_id" not in fields
    assert "payload" in fields


async def test_pulse_records_a_score_event(fake_redis: Any) -> None:
    db = FakePulseDb()
    fields = score_event_fields(
        company_id=COMPANY, tenant_id=TENANT, score=80, is_hot=True, source_version="2"
    )
    async with db.begin() as connection:
        await pulse_consumer.process_event(connection, fields)  # type: ignore[arg-type]
    assert len(db.processed) == 1


@pytest.mark.parametrize(
    "broken",
    [
        {"event_type": "signal.created"},
        {"event_id": "not-a-uuid", "event_type": "x", "payload": "{}"},
        {"event_id": str(uuid4()), "event_type": "x", "payload": "{not json"},
        {"event_id": str(uuid4()), "event_type": "x", "payload": "[1]"},
    ],
)
async def test_malformed_entry_is_dead_lettered_and_acked(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any, broken: dict[str, str]
) -> None:
    db = FakePulseDb()
    good = stream_message()
    await fake_redis.xadd(pulse_consumer.STREAM_NAME, broken)
    await fake_redis.xadd(pulse_consumer.STREAM_NAME, good)
    install_consumer(monkeypatch, db, fake_redis, max_reads=2)

    with pytest.raises(StopLoop):
        await pulse_consumer.main()

    info = await fake_redis.xpending(pulse_consumer.STREAM_NAME, pulse_consumer.GROUP_NAME)
    assert info["pending"] == 0
    dead = await fake_redis.xrange(pulse_consumer.DEAD_LETTER_STREAM)
    assert len(dead) == 1
    assert dead[0][1]["error"]
    assert dead[0][1]["source_message_id"]
    assert len(db.fact_signals) == 1
