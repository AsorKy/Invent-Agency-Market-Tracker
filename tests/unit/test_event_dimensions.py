"""Unit tests for the rollup dimensions on event payloads (REQ-PUL-002, REQ-EVT-005).

Core adds ``city``, ``service_line``, and ``hot`` to its events and Pulse reads them, so a
rollup lands in the right bucket instead of ``unknown``. Everything runs against fakes.
"""

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic")

from services.core.app.actions import OutcomeCreate, record_outcome  # noqa: E402
from services.core.app.event_dimensions import is_hot, lead_dimensions  # noqa: E402
from services.core.app.leads import LeadCreate, create_lead  # noqa: E402
from services.pulse.app import rollups  # noqa: E402

TENANT = uuid4()
LEAD_ID = uuid4()


class _Tx:
    def __init__(self, lead: dict[str, Any]) -> None:
        self.lead = lead
        self.events: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_Tx":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get_lead(self, lead_id: UUID, tenant_id: UUID | None) -> dict[str, Any]:
        return dict(self.lead)

    async def insert_outcome(self, row: dict[str, Any]) -> None:
        return None

    async def add_outbox_event(self, **event: Any) -> None:
        self.events.append(event)

    async def lock_key(self, key: str) -> None:
        return None

    async def get_idempotency(self, tenant_id: UUID, key: str) -> None:
        return None

    async def insert_lead(self, row: dict[str, Any]) -> None:
        return None

    async def save_idempotency(self, **kwargs: Any) -> None:
        return None


class _Connection:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.calls.append(params)


def _lead(stage: str) -> dict[str, Any]:
    return {
        "id": LEAD_ID,
        "tenant_id": TENANT,
        "company_id": uuid4(),
        "service_line": "saas",
        "city": "Medellin",
        "stage": stage,
        "version": 1,
    }


def test_is_hot_accepts_enum_and_string() -> None:
    assert is_hot("hot") and not is_hot("qualified") and not is_hot(None)


def test_lead_dimensions_omit_unknown_city() -> None:
    assert lead_dimensions({"service_line": "saas", "stage": "hot"}) == {
        "service_line": "saas",
        "hot": True,
    }


@pytest.mark.parametrize(("stage", "hot"), [("hot", True), ("qualified", False)])
def test_outcome_event_carries_city_service_line_and_hot(stage: str, hot: bool) -> None:
    tx = _Tx(_lead(stage))
    asyncio.run(record_outcome(tx, LEAD_ID, OutcomeCreate(outcome="contact"), tenant_id=TENANT))
    payload = tx.events[0]["payload"]
    assert (payload["city"], payload["service_line"], payload["hot"]) == ("Medellin", "saas", hot)


def test_lead_created_event_carries_dimensions() -> None:
    tx = _Tx(_lead("discovered"))
    body = LeadCreate(company_id=uuid4(), service_line="saas", city="Cali", source="manual")
    asyncio.run(create_lead(tx, body, tenant_id=TENANT, idempotency_key="k-1"))
    payload = tx.events[0]["payload"]
    assert (payload["city"], payload["service_line"], payload["hot"]) == ("Cali", "saas", False)


def test_signal_rolls_up_into_its_city_and_service_line_bucket() -> None:
    connection = _Connection()
    payload = {
        "signal_id": str(uuid4()),
        "company_id": str(uuid4()),
        "source": "rues",
        "signal_type": "new_company",
        "observed_at": datetime(2026, 1, 2, 15, 0, tzinfo=UTC).isoformat(),
        "strength": 0.5,
        "city": "Bogota",
        "service_line": "saas",
    }
    asyncio.run(rollups.apply_signal(connection, TENANT, payload))  # type: ignore[arg-type]
    params = connection.calls[0]
    assert (params["city"], params["service_line"]) == ("bogota", "saas")
    assert params["rollup_date"] == date(2026, 1, 2)


def test_outcome_rolls_up_with_hot_flag() -> None:
    connection = _Connection()
    payload = {
        "outcome_id": str(uuid4()),
        "lead_id": str(LEAD_ID),
        "outcome": "contact",
        "occurred_at": "2026-01-02T15:00:00+00:00",
        **lead_dimensions(_lead("hot")),
    }
    asyncio.run(rollups.apply_outcome(connection, TENANT, payload))  # type: ignore[arg-type]
    params = connection.calls[0]
    assert (params["city"], params["service_line"], params["hot"]) == ("medellin", "saas", 1)


def test_missing_dimensions_still_fall_back_to_unknown() -> None:
    connection = _Connection()
    payload = {
        "signal_id": str(uuid4()),
        "company_id": str(uuid4()),
        "source": "rues",
        "signal_type": "new_company",
        "observed_at": "2026-01-02T15:00:00+00:00",
        "strength": 0.5,
    }
    asyncio.run(rollups.apply_signal(connection, TENANT, payload))  # type: ignore[arg-type]
    assert connection.calls[0]["city"] == connection.calls[0]["service_line"] == "unknown"
