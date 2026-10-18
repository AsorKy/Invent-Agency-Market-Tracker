"""Unit tests for the tenant carried on every event stream entry (REQ-EVT-001, REQ-EVT-002).

They prove that ``stream_fields`` puts ``tenant_id`` in its own field, that the relay sends an
outbox row without a tenant straight to the dead letter queue (``missing_tenant_id``) with an
ALERT line and never publishes it, that republish skips such rows, and, by scanning the source,
that every INSERT into ``core.outbox_event`` under ``services/core/app/`` sets ``tenant_id``.
"""

import asyncio
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from services.core.app.dead_letter import (
    MISSING_TENANT_ERROR,
    MissingTenantError,
    relay_due_events,
    stream_fields,
)
from services.core.app.republish import replay

NOW = datetime(2026, 1, 1, tzinfo=UTC)
TENANT = UUID(int=7)
APP = Path(__file__).resolve().parents[2] / "services" / "core" / "app"


def _row(row_id: int, tenant: UUID | None) -> dict[str, Any]:
    return {
        "id": row_id,
        "event_id": uuid4(),
        "tenant_id": tenant,
        "aggregate_type": "lead",
        "aggregate_id": uuid4(),
        "event_type": "lead.created",
        "version": 1,
        "payload": {},
        "attempts": 0,
    }


class _Tx:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.published: list[int] = []
        self.dead: list[tuple[int, str]] = []

    async def __aenter__(self) -> "_Tx":
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def claim_due(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        return self.rows

    async def mark_published(self, row_id: int, now: datetime) -> None:
        self.published.append(row_id)

    async def record_failure(self, row_id: int, **_k: Any) -> None:
        raise AssertionError("no retry expected")

    async def dead_letter(self, row: dict[str, Any], *, error: str, **_k: Any) -> None:
        self.dead.append((row["id"], error))


def test_stream_fields_carry_tenant_as_own_field() -> None:
    assert stream_fields(_row(1, TENANT))["tenant_id"] == str(TENANT)
    assert stream_fields(_row(1, TENANT), replay=True, target="ml")["tenant_id"] == str(TENANT)


def test_stream_fields_refuse_a_row_without_tenant() -> None:
    with pytest.raises(MissingTenantError):
        stream_fields(_row(1, None))


def test_relay_dead_letters_null_tenant_without_publishing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tx = _Tx([_row(1, None), _row(2, TENANT)])
    sent: list[int] = []

    async def publish(row: dict[str, Any]) -> None:
        sent.append(row["id"])

    with caplog.at_level(logging.ERROR):
        counts = asyncio.run(relay_due_events(tx, publish, now=NOW))  # type: ignore[arg-type]
    assert counts == {"published": 1, "retried": 0, "dead_lettered": 1}
    assert sent == [2] and tx.published == [2]
    assert tx.dead == [(1, MISSING_TENANT_ERROR)] and MISSING_TENANT_ERROR == "missing_tenant_id"
    assert any("ALERT" in r.message and "missing_tenant_id" in r.message for r in caplog.records)


class _Locker:
    async def acquire(self, key: str, ttl_seconds: int) -> bool:
        return True

    async def release(self, key: str) -> None:
        return None

    async def exists(self, key: str) -> bool:
        return False


def test_republish_skips_rows_without_tenant() -> None:
    rows = [_row(1, TENANT), _row(2, None)]
    out: list[dict[str, str]] = []

    async def fetch(since: datetime, company: UUID | None, after: int, limit: int) -> Any:
        return [r for r in rows if r["id"] > after]

    async def publish(fields: dict[str, str]) -> None:
        out.append(fields)

    count = asyncio.run(
        replay(
            target="pulse",
            since=NOW,
            company_id=None,
            confirm=True,
            fetch_page=fetch,
            publish=publish,
            locker=_Locker(),
        )
    )
    assert count == 1 and out[0]["tenant_id"] == str(TENANT)


def test_every_outbox_insert_sets_tenant_id() -> None:
    pattern = re.compile(r"INSERT INTO core\.outbox_event\s*\(([^)]*)\)", re.IGNORECASE)
    found = 0
    for path in sorted(APP.glob("*.py")):
        for match in pattern.finditer(path.read_text()):
            found += 1
            columns = [c.strip() for c in match.group(1).split(",")]
            assert "tenant_id" in columns, f"{path.name}: outbox insert without tenant_id"
    assert found >= 5
