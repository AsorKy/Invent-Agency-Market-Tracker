"""Provide one in-memory database for the Core-to-Pulse relay tests.

``FakeCoreEngine`` imitates the SQLAlchemy async engine the real ``CoreRepository`` units of
work use, so ``create_lead``, ``record_outcome``, ``create_signal`` and ``relay_due_events`` run
their real SQL strings against dicts. ``FakePulseDb`` does the same for the Pulse consumer and
refuses a write whose tenant differs from the tenant context of the transaction, as row level
security would. It relates to ``tests/unit/test_relay_to_consumers.py`` and
``tests/unit/support_outbox.py``. Transactions stage writes and apply them only on clean exit.
"""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID


class Result:
    """Cover the result methods the repository code calls."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> "Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalar_one(self) -> Any:
        return self._scalar


class StagedTransaction:
    """Stage writes and apply them only when the block exits without an error."""

    def __init__(self, dispatch: Callable[..., Result]) -> None:
        self._dispatch = dispatch
        self.staged: list[Callable[[], None]] = []

    async def __aenter__(self) -> "StagedTransaction":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            for apply in self.staged:
                apply()
        self.staged.clear()

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Result:
        sql = " ".join(str(statement).split())
        return self._dispatch(sql, params or {}, self.staged)


class FakeCoreEngine:
    """Hold the Core tables the lead, outcome, signal and relay code paths touch."""

    def __init__(self) -> None:
        self.companies: dict[UUID, UUID] = {}
        self.leads: dict[UUID, dict[str, Any]] = {}
        self.outcomes: dict[UUID, dict[str, Any]] = {}
        self.signals: dict[UUID, dict[str, Any]] = {}
        self.idempotency: dict[tuple[UUID, str], dict[str, Any]] = {}
        self.outbox: list[dict[str, Any]] = []
        self.dead_letters: list[dict[str, Any]] = []

    def begin(self) -> StagedTransaction:
        return StagedTransaction(self._dispatch)

    async def dispose(self) -> None:
        return None

    def add_company(self, company_id: UUID, tenant_id: UUID) -> None:
        self.companies[company_id] = tenant_id

    def _append_outbox(self, params: dict[str, Any], tenant_id: UUID | None) -> None:
        self.outbox.append(
            {
                "id": len(self.outbox) + 1,
                "event_id": params["event_id"],
                "tenant_id": tenant_id,
                "aggregate_type": params.get("aggregate_type", "lead"),
                "aggregate_id": params["aggregate_id"],
                "event_type": params.get("event_type", "lead.created"),
                "version": params.get("version", 1),
                "payload": json.loads(params["payload"]),
                "attempts": 0,
                "replay": None,
                "published_at": None,
                "dead_lettered_at": None,
            }
        )

    def _dispatch(
        self, sql: str, params: dict[str, Any], staged: list[Callable[[], None]]
    ) -> Result:
        if "pg_advisory_xact_lock" in sql:
            return Result()
        if "FROM core.idempotency_key" in sql:
            return Result(
                rows=[
                    r
                    for k, r in self.idempotency.items()
                    if k[1] == params["key"] and k[0] == params["tenant_id"]
                ]
            )
        if "INSERT INTO core.idempotency_key" in sql:
            key = (params["tenant_id"], params["key"])
            row = {
                "request_hash": params["request_hash"],
                "response_body": json.loads(params["response"]),
            }
            staged.append(lambda: self.idempotency.__setitem__(key, row))
            return Result()
        if "INSERT INTO core.lead " in sql:
            staged.append(lambda: self.leads.__setitem__(params["id"], dict(params)))
            return Result()
        if "FROM core.lead" in sql and "SELECT id, tenant_id" in sql:
            lead = self.leads.get(params["id"])
            wanted = params["tenant_id"]
            if lead is None or (wanted is not None and lead["tenant_id"] != wanted):
                return Result()
            return Result(rows=[lead])
        if "INSERT INTO core.lead_outcome" in sql:
            staged.append(lambda: self.outcomes.__setitem__(params["id"], dict(params)))
            return Result()
        if "INSERT INTO core.signal" in sql:
            staged.append(lambda: self.signals.__setitem__(params["id"], dict(params)))
            return Result(scalar=params["id"])
        if "INSERT INTO core.outbox_event" in sql:
            tenant = (
                params["tenant_id"]
                if "tenant_id" in params
                else self.companies.get(params["company_id"])
            )
            fixed = (
                {"aggregate_type": "signal", "event_type": "signal.created"}
                if "'signal.created'" in sql
                else {"aggregate_type": "lead", "event_type": "lead.created"}
                if "'lead.created'" in sql
                else {}
            )
            staged.append(lambda: self._append_outbox(fixed | params, tenant))
            return Result()
        return self._relay_dispatch(sql, params, staged)

    def _relay_dispatch(
        self, sql: str, params: dict[str, Any], staged: list[Callable[[], None]]
    ) -> Result:
        by_id = {row["id"]: row for row in self.outbox}
        if "FROM core.outbox_event e" in sql:
            due = [r for r in self.outbox if not r["published_at"] and not r["dead_lettered_at"]]
            return Result(rows=[dict(r) for r in due[: params["limit"]]])
        if "SET published_at" in sql:
            staged.append(lambda: by_id[params["id"]].update(published_at=params["now"]))
            return Result()
        if "INSERT INTO core.outbox_dead_letter" in sql:
            staged.append(lambda: self.dead_letters.append(dict(params)))
            return Result()
        if "SET attempts = :attempts, last_error = :error, dead_lettered_at" in sql:
            staged.append(lambda: by_id[params["id"]].update(dead_lettered_at=params["now"]))
            return Result()
        raise AssertionError(f"unexpected SQL: {sql}")


class FakePulseDb:
    """Hold the Pulse projections and enforce the tenant policy on every write."""

    def __init__(self) -> None:
        self.processed: dict[UUID, dict[str, Any]] = {}
        self.signal_facts: dict[UUID, dict[str, Any]] = {}
        self.outcome_facts: dict[UUID, dict[str, Any]] = {}
        self.rollup: dict[tuple[Any, ...], dict[str, int]] = {}
        self._context: str | None = None

    def begin(self) -> StagedTransaction:
        return StagedTransaction(self._dispatch)

    def _check_tenant(self, params: dict[str, Any]) -> None:
        if self._context != str(params["tenant_id"]):
            raise AssertionError("write outside the tenant context of the transaction")

    def _bump(self, params: dict[str, Any], column: str, hot: int = 0) -> None:
        key = (params["tenant_id"], params["rollup_date"], params["city"], params["service_line"])
        row = self.rollup.setdefault(key, {"signal_count": 0, "lead_count": 0, "hot_lead_count": 0})
        row[column] += 1
        row["hot_lead_count"] += hot

    def _dispatch(
        self, sql: str, params: dict[str, Any], staged: list[Callable[[], None]]
    ) -> Result:
        if "set_config('app.tenant_id'" in sql:
            self._context = params["tenant_id"]
            return Result()
        self._check_tenant(params)
        if "INSERT INTO pulse.processed_event" in sql:
            event_id = params["event_id"]
            if event_id in self.processed:
                return Result()
            staged.append(lambda: self.processed.setdefault(event_id, dict(params)))
            return Result(scalar=event_id)
        if "INSERT INTO pulse.fact_signal" in sql:
            signal_id = params["signal_id"]
            if signal_id not in self.signal_facts:
                staged.append(lambda: self.signal_facts.setdefault(signal_id, dict(params)))
                staged.append(lambda: self._bump(params, "signal_count"))
            return Result()
        if "INSERT INTO pulse.fact_lead_outcome" in sql:
            outcome_id = params["outcome_id"]
            if outcome_id not in self.outcome_facts:
                staged.append(lambda: self.outcome_facts.setdefault(outcome_id, dict(params)))
                staged.append(lambda: self._bump(params, "lead_count", params["hot"]))
            return Result()
        raise AssertionError(f"unexpected SQL: {sql}")
