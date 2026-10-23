"""Conversion links the lead to its project in the same transaction (T-181, REQ-LNS-015)."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("sqlalchemy")

from services.core.app.repository import SqlActionTransaction  # noqa: E402


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.calls.append((str(statement), params))


def test_insert_project_sets_the_lead_project_id() -> None:
    connection = _Connection()
    now = datetime(2026, 10, 23, tzinfo=UTC)
    row = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "lead_id": uuid4(),
        "name": "Pilot",
        "service_line": "ops",
        "hypothesis": "h",
        "status": "draft",
        "estimated_roi": None,
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    asyncio.run(SqlActionTransaction(connection).insert_project(row))  # type: ignore[arg-type]
    (insert, _), (update, params) = connection.calls
    assert "INSERT INTO core.project" in insert
    assert "UPDATE core.lead SET project_id" in update and "project_id IS NULL" in update
    assert params == {
        "project_id": row["id"],
        "lead_id": row["lead_id"],
        "tenant_id": row["tenant_id"],
    }
