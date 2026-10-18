"""Unit tests for Pulse incremental rollups (REQ-PUL-002).

The database is an in-memory fake that dispatches on SQL text and mimics the
one-statement fact insert plus counter upsert. No external service is used.
"""

import json
from datetime import date
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic_settings")

from services.pulse.app import consumer, rollup_close  # noqa: E402

TENANT = uuid4()


class _Result:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class FakeDb:
    def __init__(self) -> None:
        self.processed: set[Any] = set()
        self.facts: set[Any] = set()
        self.rollup: dict[tuple[Any, ...], dict[str, Any]] = {}

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        p = params or {}
        if "set_config" in sql:
            return _Result()
        if "INSERT INTO pulse.processed_event" in sql:
            if p["event_id"] in self.processed:
                return _Result()
            self.processed.add(p["event_id"])
            return _Result(p["event_id"])
        if "INSERT INTO pulse.fact_signal" in sql or "INSERT INTO pulse.fact_lead_outcome" in sql:
            fact_id = p.get("signal_id") or p["outcome_id"]
            if fact_id in self.facts:
                return _Result()
            self.facts.add(fact_id)
            key = (p["tenant_id"], p["rollup_date"], p["city"], p["service_line"])
            row = self.rollup.setdefault(
                key, {"signal_count": 0, "lead_count": 0, "hot_lead_count": 0, "low_sample": None}
            )
            if "signal_id" in p:
                row["signal_count"] += 1
            else:
                row["lead_count"] += 1
                row["hot_lead_count"] += p["hot"]
            return _Result()
        if "UPDATE pulse.daily_rollup" in sql:
            for key, row in self.rollup.items():
                if key[0] == p["tenant_id"] and key[1] == p["rollup_date"]:
                    row["low_sample"] = row["signal_count"] < p["threshold"]
            return _Result()
        raise AssertionError(f"unexpected SQL: {sql}")


def message(event_type: str, payload: dict[str, Any]) -> dict[str, str]:
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "tenant_id": str(TENANT),
        "payload": json.dumps(payload),
    }


def signal(observed_at: str, city: str = "bogota") -> dict[str, Any]:
    return {
        "signal_id": str(uuid4()),
        "company_id": str(uuid4()),
        "source": "rues",
        "signal_type": "new_company",
        "observed_at": observed_at,
        "strength": 0.5,
        "city": city,
        "service_line": "web",
    }


async def run(db: FakeDb, event: dict[str, str]) -> None:
    await consumer.process_event(db, event)  # type: ignore[arg-type]


async def test_signal_counts_local_day_not_utc_day() -> None:
    db = FakeDb()
    # 03:30Z on Jan 2 is 22:30 on Jan 1 in Bogota (UTC-5).
    await run(db, message("signal.created", signal("2026-01-02T03:30:00Z")))
    assert set(db.rollup) == {(TENANT, date(2026, 1, 1), "bogota", "web")}


async def test_only_affected_day_changes() -> None:
    db = FakeDb()
    await run(db, message("signal.created", signal("2026-01-02T15:00:00Z")))
    before = {k: dict(v) for k, v in db.rollup.items()}
    await run(db, message("signal.created", signal("2026-01-03T15:00:00Z")))
    day1 = (TENANT, date(2026, 1, 2), "bogota", "web")
    assert db.rollup[day1] == before[day1]
    assert db.rollup[(TENANT, date(2026, 1, 3), "bogota", "web")]["signal_count"] == 1


async def test_replayed_event_and_fact_count_once() -> None:
    db = FakeDb()
    event = message("signal.created", signal("2026-01-02T15:00:00Z"))
    await run(db, event)
    await run(db, event)
    assert next(iter(db.rollup.values()))["signal_count"] == 1
    # Same fact under a new event id is still counted once.
    await run(db, {**event, "event_id": str(uuid4())})
    assert next(iter(db.rollup.values()))["signal_count"] == 1


async def test_outcome_updates_lead_counters() -> None:
    db = FakeDb()
    payload = {
        "outcome_id": str(uuid4()),
        "lead_id": str(uuid4()),
        "outcome": "won",
        "occurred_at": "2026-01-02T15:00:00Z",
        "city": "medellin",
        "service_line": "web",
        "hot": True,
    }
    await run(db, message("lead.outcome_recorded", payload))
    row = db.rollup[(TENANT, date(2026, 1, 2), "medellin", "web")]
    assert (row["lead_count"], row["hot_lead_count"], row["signal_count"]) == (1, 1, 0)


async def test_missing_dimensions_use_unknown() -> None:
    db = FakeDb()
    payload = signal("2026-01-02T15:00:00Z")
    del payload["city"], payload["service_line"]
    await run(db, message("signal.created", payload))
    assert set(db.rollup) == {(TENANT, date(2026, 1, 2), "unknown", "unknown")}


async def test_close_marks_low_sample_for_given_day_only() -> None:
    db = FakeDb()
    await run(db, message("signal.created", signal("2026-01-02T15:00:00Z")))
    await run(db, message("signal.created", signal("2026-01-03T15:00:00Z")))
    await rollup_close.close_day(db, TENANT, date(2026, 1, 2))  # type: ignore[arg-type]
    assert db.rollup[(TENANT, date(2026, 1, 2), "bogota", "web")]["low_sample"] is True
    assert db.rollup[(TENANT, date(2026, 1, 3), "bogota", "web")]["low_sample"] is None


def test_default_close_day_is_previous_local_day() -> None:
    from datetime import UTC, datetime

    # 02:00Z on Jan 3 is still Jan 2 locally, so the last closed day is Jan 1.
    assert rollup_close.default_close_day(datetime(2026, 1, 3, 2, tzinfo=UTC)) == date(2026, 1, 1)


def test_cli_requires_tenant() -> None:
    with pytest.raises(SystemExit):
        rollup_close.parse_args([])
    args = rollup_close.parse_args(["--tenant", str(TENANT), "--day", "2026-01-02"])
    assert args.tenant == [TENANT] and args.day == date(2026, 1, 2)
