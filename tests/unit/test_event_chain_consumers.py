"""Prove unknown and chain-only event types are acknowledged, never dead-lettered (T-149).

REQ-EXT-001, REQ-EVT-002, REQ-LNS-012. The relay runs on in-memory fakes; the pulse and ML
consumers run ``process_event`` on a fake connection. No external service is used.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("sqlalchemy")
pytest.importorskip("structlog")
pytest.importorskip("redis.asyncio")

from libs.domain.event_chain import CHAIN  # noqa: E402
from services.core.app import dead_letter  # noqa: E402
from services.ml.app import consumer as ml_consumer  # noqa: E402
from services.pulse.app import consumer as pulse_consumer  # noqa: E402

TENANT = uuid4()
UNKNOWN = "vendor.never_heard_of_it"
TYPES = [UNKNOWN, *(name for name in CHAIN if name != "signal.created")]


class _Result:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class FakeConnection:
    def __init__(self, first_seen: bool = True) -> None:
        self.first_seen = first_seen
        self.sql: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.sql.append(sql)
        if "processed_event" in sql:
            return _Result(params["event_id"] if self.first_seen else None)
        return _Result()


def message(event_type: str) -> dict[str, str]:
    return {
        "event_id": str(uuid4()),
        "tenant_id": str(TENANT),
        "event_type": event_type,
        "aggregate_type": "signal",
        "aggregate_id": str(uuid4()),
        "version": "1",
        "payload": json.dumps({"company_id": str(uuid4()), "source_version": "x-v1"}),
    }


class FakeTx:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.published: list[int] = []
        self.dead: list[int] = []
        self.failed: list[int] = []

    async def claim_due(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        return self.rows

    async def mark_published(self, row_id: int, now: datetime) -> None:
        self.published.append(row_id)

    async def record_failure(self, row_id: int, **_: Any) -> None:
        self.failed.append(row_id)

    async def dead_letter(self, row: dict[str, Any], **_: Any) -> None:
        self.dead.append(row["id"])


class FakeUnitOfWork:
    def __init__(self, tx: FakeTx) -> None:
        self.tx = tx

    async def __aenter__(self) -> FakeTx:
        return self.tx

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.mark.parametrize("event_type", TYPES)
def test_relay_publishes_unknown_and_chain_types_without_dead_letter(event_type: str) -> None:
    row = {
        "id": 1,
        "event_id": uuid4(),
        "tenant_id": TENANT,
        "event_type": event_type,
        "aggregate_type": "signal",
        "aggregate_id": uuid4(),
        "version": 1,
        "payload": {"source_version": "x-v1"},
        "attempts": 0,
    }
    tx = FakeTx([row])
    published: list[dict[str, str]] = []

    async def publish(item: dict[str, Any]) -> None:
        published.append(dead_letter.stream_fields(item))

    counts = asyncio.run(
        dead_letter.relay_due_events(FakeUnitOfWork(tx), publish, now=datetime.now(UTC))
    )
    assert counts == {"published": 1, "retried": 0, "dead_lettered": 0}
    assert tx.published == [1] and tx.dead == [] and tx.failed == []
    assert published[0]["event_type"] == event_type


@pytest.mark.parametrize("event_type", TYPES)
def test_pulse_consumer_records_and_ignores_other_types(event_type: str) -> None:
    connection = FakeConnection()
    asyncio.run(pulse_consumer.process_event(connection, message(event_type)))  # type: ignore[arg-type]
    assert any("INSERT INTO pulse.processed_event" in sql for sql in connection.sql)
    assert not any("fact_signal" in sql or "daily_rollup" in sql for sql in connection.sql)
    pulse_consumer.parse_event(message(event_type))


@pytest.mark.parametrize("event_type", TYPES)
def test_ml_consumer_records_and_ignores_other_types(event_type: str) -> None:
    connection = FakeConnection()
    asyncio.run(ml_consumer.process_event(connection, message(event_type)))  # type: ignore[arg-type]
    assert len(connection.sql) == 1 and "ml.processed_event" in connection.sql[0]


def test_ml_consumer_still_marks_score_stale_for_known_type() -> None:
    connection = FakeConnection()
    asyncio.run(ml_consumer.process_event(connection, message("signal.created")))  # type: ignore[arg-type]
    assert any("UPDATE ml.lead_score" in sql for sql in connection.sql)

