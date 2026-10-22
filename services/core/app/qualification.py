"""Create or advance leads automatically from ``lead.score_updated`` events.

``handle_score_event`` records the event in the inbox (``core.processed_event``), reads the
tenant thresholds (``brief_rules.alert_rules.min_score_for_hot`` and ``min_score_for_qualified``),
and creates the lead at Discovered or advances it step by step to Qualified or Hot, all in one
``QualificationUnitOfWork`` transaction. It never moves a lead backward and never touches a lead
whose stage a human set (a ``lead.status_changed`` audit entry exists) or that has left the
automatic stages. Each automatic move writes ``audit_log``; new leads emit ``lead.created``. It
relates to ``libs/domain/pipeline.py``, ``tenant_config.py``, ``worker.py`` and
``repository.py`` (REQ-COR-008). Cost is O(leads of the company) per event.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID, uuid4

from libs.domain.pipeline import PipelineStage, can_transition
from services.core.app.hot_alert import HotAlert, HotCallback, fire
from services.core.app.tenant_config import row_to_config

AUTO_STAGES = (PipelineStage.DISCOVERED, PipelineStage.QUALIFIED, PipelineStage.HOT)
DEFAULT_SERVICE_LINE = "general"


class QualificationTransaction(Protocol):
    async def set_tenant(self, tenant_id: UUID) -> None: ...
    async def mark_processed(self, event_id: UUID) -> bool: ...
    async def lock_key(self, key: str) -> None: ...
    async def get_company_tenant(self, company_id: UUID) -> tuple[bool, UUID | None]: ...
    async def get_config(self, tenant_id: UUID) -> dict[str, Any] | None: ...
    async def find_leads(self, company_id: UUID) -> list[dict[str, Any]]: ...
    async def has_manual_transition(self, lead_id: UUID) -> bool: ...
    async def insert_lead(self, row: dict[str, Any]) -> None: ...
    async def update_stage(self, row: dict[str, Any]) -> None: ...
    async def add_audit(self, *, action: str, lead_id: UUID, payload: dict[str, Any]) -> None: ...
    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_id: UUID,
        version: int,
        payload: dict[str, Any],
    ) -> None: ...


class QualificationUnitOfWork(Protocol):
    async def __aenter__(self) -> QualificationTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


def target_stage(score: int, hot_min: int, qualified_min: int) -> PipelineStage:
    """Map a score to the stage it deserves."""
    if score >= hot_min:
        return PipelineStage.HOT
    if score >= qualified_min:
        return PipelineStage.QUALIFIED
    return PipelineStage.DISCOVERED


def advance_path(current: PipelineStage, target: PipelineStage) -> list[PipelineStage]:
    """Return the forward steps from ``current`` to ``target`` (empty when none)."""
    if current not in AUTO_STAGES or target not in AUTO_STAGES:
        return []
    start, end = AUTO_STAGES.index(current), AUTO_STAGES.index(target)
    steps = list(AUTO_STAGES[start + 1 : end + 1])
    previous = current
    for step in steps:
        if not can_transition(previous, step):  # pragma: no cover - guards a rule change
            return []
        previous = step
    return steps


def parse_score_event(fields: Mapping[str, str]) -> tuple[UUID, UUID, int, str | None]:
    """Return ``(event_id, company_id, score, service_line)`` or raise ``ValueError``."""
    try:
        event_id = UUID(fields["event_id"])
        company_id = UUID(fields["aggregate_id"])
        score = int(fields["score"])
    except (KeyError, ValueError) as exc:
        raise ValueError("invalid_score_event") from exc
    if not 0 <= score <= 100:
        raise ValueError("invalid_score_event")
    return event_id, company_id, score, fields.get("service_line") or None


def _event_tenant(fields: Mapping[str, str]) -> UUID | None:
    try:
        return UUID(fields["tenant_id"])
    except (KeyError, ValueError):
        return None


async def handle_score_event(
    unit_of_work: QualificationUnitOfWork,
    fields: Mapping[str, str],
    *,
    now: datetime | None = None,
    on_hot: HotCallback | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Apply one event; return ``duplicate``, ``unknown_company``, ``created`` or ``processed``."""
    event_id, company_id, score, service_line = parse_score_event(fields)
    moment = now or datetime.now(UTC)
    alerts: list[HotAlert] = []
    event_tenant = _event_tenant(fields)
    async with unit_of_work as tx:
        if event_tenant is not None:
            # Row level security hides every tenant row until the context is set, so this
            # must run before the first read or write (inbox included). An event without a
            # tenant sees only companies without tenant.
            await tx.set_tenant(event_tenant)
        if not await tx.mark_processed(event_id):
            return "duplicate"
        known, tenant_id = await tx.get_company_tenant(company_id)
        if not known:
            return "unknown_company"
        if event_tenant is not None and tenant_id != event_tenant:
            return "unknown_company"  # defense in depth beside RLS
        await tx.lock_key(f"qualify:{company_id}")
        rules = row_to_config(
            (await tx.get_config(tenant_id) if tenant_id else None) or {}
        ).brief_rules.alert_rules
        target = target_stage(score, rules.min_score_for_hot, rules.min_score_for_qualified)
        leads = await tx.find_leads(company_id)
        outcome = "processed"
        if not leads:
            row: dict[str, Any] = {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "company_id": company_id,
                "service_line": service_line or DEFAULT_SERVICE_LINE,
                "city": None,
                "source": "auto_qualification",
                "stage": PipelineStage.DISCOVERED.value,
                "qualification_reason": {"score": score},
                "version": 1,
                "created_at": moment,
                "updated_at": moment,
            }
            await tx.insert_lead(row)
            await tx.add_outbox_event(
                event_id=uuid4(),
                tenant_id=tenant_id,
                aggregate_id=row["id"],
                version=1,
                payload={
                    "lead_id": str(row["id"]),
                    "company_id": str(company_id),
                    "service_line": row["service_line"],
                    "stage": row["stage"],
                    "origin": "auto_qualification",
                    "request_id": None,
                },
            )
            leads = [row]
            outcome = "created"
        for lead in leads:
            version = await _advance(tx, lead, target, score, moment)
            if version is not None:
                alerts.append(HotAlert(tenant_id, lead["id"], company_id, version, score, 0.0))
    stamped = clock()
    await fire(on_hot, [replace(alert, committed_at=stamped) for alert in alerts])
    return outcome


async def _advance(
    tx: QualificationTransaction,
    lead: dict[str, Any],
    target: PipelineStage,
    score: int,
    moment: datetime,
) -> int | None:
    """Advance one lead; return the new version when it reached Hot, else None."""
    current = PipelineStage(lead["stage"])
    steps = advance_path(current, target)
    if not steps or await tx.has_manual_transition(lead["id"]):
        return None
    version = int(lead["version"])
    for step in steps:
        version += 1
        await tx.update_stage(
            {
                "id": lead["id"],
                "stage": step.value,
                "version": version,
                "updated_at": moment,
                "last_transition_at": moment,
            }
        )
        await tx.add_audit(
            action="lead.auto_qualified",
            lead_id=lead["id"],
            payload={"from": current.value, "to": step.value, "score": score, "version": version},
        )
        current = step
    return version if current is PipelineStage.HOT else None
