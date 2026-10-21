"""Evaluate a funnel gate for a project, audit the decision and emit ``funnel.*`` events.

One ``FunnelUnitOfWork`` transaction holds the project lookup, the ``core.audit_log`` row
(``funnel.gate_decision`` with inputs and checklist version) and the outbox events, so they commit
or roll back together (REQ-LNS-009). The verdict comes from ``libs/domain/funnel.py``. Events:
``funnel.gate_decision`` always, ``funnel.pivot_requested`` on PIVOT, ``funnel.project_killed`` and
``funnel.kill_trigger_event`` on KILL. No HTTP route is registered yet (follow-up). O(items).
"""

from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID, uuid4

from libs.contracts.events import ReservedEventType as EventType
from libs.domain.funnel import Checklist, GateDecision, Verdict, evaluate_gate, load_checklist


class ProjectNotFoundError(Exception):
    """The project does not exist for the tenant."""


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
) -> GateDecision:
    """Return the decision after auditing it and queueing its events."""
    loaded = checklist or load_checklist(gate)
    decision = evaluate_gate(loaded, inputs)
    moment = now or datetime.now(UTC)
    async with unit_of_work as tx:
        project = await tx.get_project(project_id, tenant_id)
        if project is None:
            raise ProjectNotFoundError(str(project_id))
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
            await tx.add_outbox_event(
                event_id=uuid4(),
                tenant_id=row_tenant,
                aggregate_type="project",
                aggregate_id=project_id,
                event_type=kind.value,
                payload={k: v for k, v in body.items() if k != "inputs"}
                | {"inputs": decision.inputs},
            )
    return decision
