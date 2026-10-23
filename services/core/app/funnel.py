"""Evaluate a funnel gate for a project, audit the decision and emit ``funnel.*`` events.

One ``FunnelUnitOfWork`` transaction holds the project lookup, the ``core.audit_log`` row
(``funnel.gate_decision`` with inputs and checklist version) and the outbox events, so they commit
or roll back together (REQ-LNS-009). The verdict comes from ``libs/domain/funnel.py``. Events:
``funnel.gate_decision`` always, ``funnel.pivot_requested`` on PIVOT, ``funnel.project_killed`` and
``funnel.kill_trigger_event`` on KILL. With ``apply_kill`` a KILL also sets ``core.project.status``
to ``KILLED_STATUS`` in the same transaction (optimistic version check) and a project that is
already killed refuses further decisions. Every decision first takes the project row lock
(``SELECT ... FOR UPDATE``), then reads the status, then writes, so a concurrent ADVANCE and KILL
run in turn and the later one gets 409 ``project_killed``. The SQL transaction and the read of
the checklist state of a project live at the end of this file; ``main.py`` serves both under
``/internal/core/funnel/``. O(items).
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from libs.contracts.events import ReservedEventType as EventType
from libs.domain.event_chain import build_source_version, stamp_payload
from libs.domain.funnel import (
    GATES,
    Checklist,
    ChecklistError,
    ExampleChecklistRefusedError,
    GateDecision,
    Verdict,
    evaluate_gate,
    load_checklist,
)
from services.core.app.repository import SqlFunnelTransaction, SqlFunnelUnitOfWork

# Plain text value of core.project.status after a KILL decision (the column is unconstrained text).
KILLED_STATUS = "killed"


class ProjectNotFoundError(Exception):
    """The project does not exist for the tenant."""


class ProjectKilledError(Exception):
    """The project is already killed; no further gate decision is accepted."""


class StaleProjectVersionError(Exception):
    """The project version differs from the version the caller decided on."""


class FunnelTransaction(Protocol):
    async def get_project(
        self, project_id: UUID, tenant_id: UUID | None
    ) -> dict[str, Any] | None: ...
    async def add_audit(
        self,
        *,
        action: str,
        project_id: UUID,
        tenant_id: UUID | None,
        payload: dict[str, Any],
    ) -> None: ...
    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None: ...


class FunnelUnitOfWork(Protocol):
    async def __aenter__(self) -> FunnelTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


async def evaluate_project_gate(
    unit_of_work: FunnelUnitOfWork,
    project_id: UUID,
    gate: str,
    inputs: dict[str, Any],
    *,
    tenant_id: UUID | None = None,
    checklist: Checklist | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
    parent_event: dict[str, Any] | None = None,
    apply_kill: bool = False,
    expected_version: int | None = None,
    on_gate_event: Callable[[dict[str, Any]], None] | None = None,
) -> GateDecision:
    """Return the decision after auditing it and queueing its events.

    ``apply_kill`` needs a transaction with ``mark_killed`` (see ``SqlFunnelStatusTransaction``).
    ``on_gate_event`` receives the written ``funnel.gate_decision`` event as a parent mapping
    (``event_id``, ``event_type``, ``source_version``) once it is queued; a caller passes its
    ``event_id`` as ``LeadStatusPatch.gate_event_id`` (T-188).
    The decision and the response do not change.
    """
    loaded = checklist or load_checklist(gate)
    decision = evaluate_gate(loaded, inputs)
    moment = now or datetime.now(UTC)
    async with unit_of_work as tx:
        project = await tx.get_project(project_id, tenant_id)
        if project is None:
            raise ProjectNotFoundError(str(project_id))
        if project.get("status") == KILLED_STATUS:
            raise ProjectKilledError(str(project_id))
        version = project.get("version")
        if expected_version is not None and version != expected_version:
            raise StaleProjectVersionError(str(project_id))
        row_tenant = project.get("tenant_id")
        body = {
            "project_id": str(project_id),
            "gate": decision.gate,
            "verdict": decision.verdict.value,
            "checklist_version": decision.checklist_version,
            "example_checklist": decision.example_checklist,
            "score": decision.score,
            "reasons": decision.reasons,
            "kill_triggers": decision.kill_triggers,
            "items": [item.model_dump() for item in decision.items],
            "inputs": decision.inputs,
            "decided_at": moment.isoformat(),
            "request_id": request_id,
        }
        producer_version = f"funnel-{decision.checklist_version}"
        if parent_event is None:
            body["source_version"] = build_source_version(producer_version)
        else:
            body = stamp_payload(body, "funnel.gate_decision", producer_version, parent_event)
        await tx.add_audit(
            action="funnel.gate_decision",
            project_id=project_id,
            tenant_id=row_tenant,
            payload=body,
        )
        kinds = [EventType.FUNNEL_GATE_DECISION]
        if decision.verdict is Verdict.PIVOT:
            kinds.append(EventType.FUNNEL_PIVOT_REQUESTED)
        elif decision.verdict is Verdict.KILL:
            kinds += [EventType.FUNNEL_PROJECT_KILLED, EventType.FUNNEL_KILL_TRIGGER_EVENT]
        for kind in kinds:
            event_id = uuid4()
            if kind is EventType.FUNNEL_GATE_DECISION and on_gate_event is not None:
                on_gate_event(
                    {
                        "event_id": str(event_id),
                        "event_type": kind.value,
                        "source_version": str(body["source_version"]),
                    }
                )
            await tx.add_outbox_event(
                event_id=event_id,
                tenant_id=row_tenant,
                aggregate_type="project",
                aggregate_id=project_id,
                event_type=kind.value,
                payload={k: v for k, v in body.items() if k != "inputs"}
                | {"inputs": decision.inputs},
            )
        if apply_kill and decision.verdict is Verdict.KILL:
            changed = await tx.mark_killed(  # type: ignore[attr-defined]
                project_id, row_tenant, expected_version=version
            )
            if not changed:
                raise StaleProjectVersionError(str(project_id))
    return decision


class SqlFunnelStatusTransaction(SqlFunnelTransaction):
    """Tenant scoped project reads and the optimistic KILL status write (REQ-SEC-005)."""

    async def get_project(self, project_id: UUID, tenant_id: UUID | None) -> dict[str, Any] | None:
        """Lock the project row, then read it: concurrent decisions on a project run in turn."""
        locked = await self.connection.execute(
            text(
                """
                SELECT id FROM core.project
                WHERE id = :id AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {"id": project_id, "tenant_id": tenant_id},
        )
        if locked.first() is None:
            return None
        return await self.read_project(project_id, tenant_id)  # type: ignore[arg-type]

    async def read_project(self, project_id: UUID, tenant_id: UUID) -> dict[str, Any] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT id, tenant_id, name, status,
                       COALESCE((metadata->>'version')::integer, 1) AS version
                FROM core.project
                WHERE id = :id AND tenant_id = :tenant_id
                """
            ),
            {"id": project_id, "tenant_id": tenant_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def last_decisions(self, project_id: UUID, tenant_id: UUID) -> list[dict[str, Any]]:
        result = await self.connection.execute(
            text(
                """
                SELECT payload, created_at FROM core.audit_log
                WHERE tenant_id = :tenant_id AND aggregate_type = 'project'
                  AND aggregate_id = :id AND action = 'funnel.gate_decision'
                ORDER BY id DESC
                LIMIT 100
                """
            ),
            {"id": project_id, "tenant_id": tenant_id},
        )
        return [dict(row) for row in result.mappings().all()]

    async def mark_killed(
        self, project_id: UUID, tenant_id: UUID | None, *, expected_version: int
    ) -> bool:
        result = await self.connection.execute(
            text(
                """
                UPDATE core.project
                SET status = :status,
                    metadata = jsonb_set(
                        metadata, '{version}', to_jsonb(CAST(:new_version AS integer)), true),
                    updated_at = now()
                WHERE id = :id AND tenant_id = :tenant_id AND status <> :status
                  AND COALESCE((metadata->>'version')::integer, 1) = :expected
                """
            ),
            {
                "id": project_id,
                "tenant_id": tenant_id,
                "status": KILLED_STATUS,
                "expected": expected_version,
                "new_version": expected_version + 1,
            },
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1


class SqlFunnelStatusUnitOfWork(SqlFunnelUnitOfWork):
    """Commit-or-rollback boundary; ``tenant_id`` is set before the first read."""

    async def __aenter__(self) -> SqlFunnelStatusTransaction:  # type: ignore[override]
        return SqlFunnelStatusTransaction((await super().__aenter__()).connection)


def funnel_unit_of_work(engine: AsyncEngine | None, tenant_id: UUID) -> SqlFunnelStatusUnitOfWork:
    if engine is None:
        raise RuntimeError("DATABASE_URL is required for core writes")
    return SqlFunnelStatusUnitOfWork(engine, tenant_id)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def read_funnel_state(
    unit_of_work: SqlFunnelStatusUnitOfWork, project_id: UUID, tenant_id: UUID
) -> dict[str, Any]:
    """Return project status and version, each gate checklist and its latest decision."""
    async with unit_of_work as tx:
        project = await tx.read_project(project_id, tenant_id)
        if project is None:
            raise ProjectNotFoundError(str(project_id))
        rows = await tx.last_decisions(project_id, tenant_id)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = _json(row["payload"])
        gate = payload.get("gate")
        if gate in GATES and gate not in latest:
            latest[gate] = {
                "verdict": payload.get("verdict"),
                "score": payload.get("score"),
                "reasons": payload.get("reasons", []),
                "checklist_version": payload.get("checklist_version"),
                "decided_at": payload.get("decided_at"),
            }
    gates: list[dict[str, Any]] = []
    for gate in GATES:
        entry: dict[str, Any] = {"gate": gate, "last_decision": latest.get(gate)}
        try:
            loaded = load_checklist(gate)
        except ExampleChecklistRefusedError:
            entry |= {"available": False, "reason": "example_checklist_refused"}
        except ChecklistError:
            entry |= {"available": False, "reason": "checklist_invalid"}
        else:
            entry |= {
                "available": True,
                "name": loaded.name,
                "checklist_version": loaded.version,
                "example_checklist": loaded.example,
                "items": [item.model_dump() for item in loaded.items],
                "rules": loaded.rules.model_dump(),
            }
        gates.append(entry)
    return {
        "project_id": str(project_id),
        "name": project["name"],
        "status": project["status"],
        "version": project["version"],
        "killed": project["status"] == KILLED_STATUS,
        "gates": gates,
    }
