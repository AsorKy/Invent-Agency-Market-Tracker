"""Apply incremental updates to ``pulse.daily_rollup`` for one local day.

The consumer calls this module for ``signal.created`` and
``lead.outcome_recorded`` (REQ-PUL-002, ADR-IMT-0001 8.1 and E1.3). Each event
touches only the row of its own local day, city, and service line. The day is
always computed with ``local_day(ts, city)``. The fact insert and the counter
upsert share one SQL statement, so a replayed fact never counts twice and the
rollup commits with the fact. It relates to ``consumer.py`` and
``rollup_close.py``.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from libs.common.time import local_day

UNKNOWN = "unknown"

# The fact insert is the first statement of the CTE, so its row count gates the
# counter update: ON CONFLICT DO NOTHING returns no row for a replayed fact.
SIGNAL_SQL = """
WITH inserted AS (
    INSERT INTO pulse.fact_signal
        (signal_id, tenant_id, company_id, source, signal_type, observed_at, strength)
    VALUES (:signal_id, :tenant_id, :company_id, :source, :signal_type,
            :observed_at, :strength)
    ON CONFLICT (signal_id) DO NOTHING
    RETURNING signal_id
)
INSERT INTO pulse.daily_rollup
    (tenant_id, rollup_date, city, service_line, signal_count, as_of, stale)
SELECT :tenant_id, :rollup_date, :city, :service_line, 1, now(), false
FROM inserted
ON CONFLICT (tenant_id, rollup_date, city, service_line) DO UPDATE
SET signal_count = pulse.daily_rollup.signal_count + 1,
    as_of = now(),
    stale = false
"""

OUTCOME_SQL = """
WITH inserted AS (
    INSERT INTO pulse.fact_lead_outcome
        (outcome_id, tenant_id, lead_id, outcome, occurred_at)
    VALUES (:outcome_id, :tenant_id, :lead_id, :outcome, :occurred_at)
    ON CONFLICT (outcome_id) DO NOTHING
    RETURNING outcome_id
)
INSERT INTO pulse.daily_rollup
    (tenant_id, rollup_date, city, service_line, lead_count, hot_lead_count, as_of, stale)
SELECT :tenant_id, :rollup_date, :city, :service_line, 1, :hot, now(), false
FROM inserted
ON CONFLICT (tenant_id, rollup_date, city, service_line) DO UPDATE
SET lead_count = pulse.daily_rollup.lead_count + 1,
    hot_lead_count = pulse.daily_rollup.hot_lead_count + EXCLUDED.hot_lead_count,
    as_of = now(),
    stale = false
"""


def parse_instant(value: str) -> datetime:
    """Parse an ISO 8601 instant that carries an offset or a trailing ``Z``."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _dimensions(payload: dict[str, object], instant: datetime) -> tuple[date, str, str]:
    city = str(payload.get("city") or UNKNOWN).strip().lower() or UNKNOWN
    service_line = str(payload.get("service_line") or UNKNOWN)
    return local_day(instant, city), city, service_line


async def apply_signal(
    connection: AsyncConnection, tenant_id: UUID, payload: dict[str, object]
) -> None:
    """Store a signal fact and bump signal_count on its local day only."""
    observed_at = parse_instant(str(payload["observed_at"]))
    rollup_date, city, service_line = _dimensions(payload, observed_at)
    await connection.execute(
        text(SIGNAL_SQL),
        {
            "signal_id": UUID(str(payload["signal_id"])),
            "tenant_id": tenant_id,
            "company_id": UUID(str(payload["company_id"])),
            "source": payload["source"],
            "signal_type": payload["signal_type"],
            "observed_at": observed_at,
            "strength": payload["strength"],
            "rollup_date": rollup_date,
            "city": city,
            "service_line": service_line,
        },
    )


async def apply_outcome(
    connection: AsyncConnection, tenant_id: UUID, payload: dict[str, object]
) -> None:
    """Store a lead outcome fact and bump lead counters on its local day only."""
    occurred_at = parse_instant(str(payload["occurred_at"]))
    rollup_date, city, service_line = _dimensions(payload, occurred_at)
    await connection.execute(
        text(OUTCOME_SQL),
        {
            "outcome_id": UUID(str(payload["outcome_id"])),
            "tenant_id": tenant_id,
            "lead_id": UUID(str(payload["lead_id"])),
            "outcome": payload["outcome"],
            "occurred_at": occurred_at,
            "rollup_date": rollup_date,
            "city": city,
            "service_line": service_line,
            "hot": 1 if payload.get("hot") is True else 0,
        },
    )
