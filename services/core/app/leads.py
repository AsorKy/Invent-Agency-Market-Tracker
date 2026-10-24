"""Create leads idempotently and move them through the pipeline with optimistic locking.

``create_lead`` replays the stored response for a repeated ``Idempotency-Key`` and writes the lead
plus a ``lead.created`` outbox event in one ``LeadUnitOfWork`` transaction. ``change_status``
locks the lead row, applies ``libs/domain/pipeline.py`` (version check, then transition rule), bumps
``version``, and writes ``audit_log``. It relates to ``main.py`` (routes) and ``repository.py``
(SQL unit of work). Each call is O(1) in queries (REQ-COR-002).
"""

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from libs.common.settings import get_settings
from libs.domain.event_chain import chain_events_after, reference_parent
from libs.domain.pipeline import (
    InvalidTransitionError,
    PipelineStage,
    VersionConflictError,
    validate_transition,
)
from services.core.app.event_dimensions import lead_dimensions
from services.core.app.hot_alert import HotAlert, HotCallback, fire

CHAIN_PRODUCER_VERSION = "core-chain-v1"
IDEMPOTENCY_TTL = timedelta(hours=24)
INITIAL_STAGE = PipelineStage.DISCOVERED


class LeadCreate(BaseModel):
    """Manual lead creation body."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    service_line: str = Field(min_length=1, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    source: str | None = Field(default=None, max_length=120)
    qualification_reason: dict[str, Any] = Field(default_factory=dict)
    scored_event_id: UUID | None = None


class LeadStatusPatch(BaseModel):
    """Status change body; ``expected_version`` is mandatory."""

    model_config = ConfigDict(extra="forbid")

    stage: PipelineStage
    expected_version: int = Field(ge=1)
    gate_event_id: UUID | None = None


class IdempotencyKeyReuseError(Exception):
    """The key was already used with a different request body."""


class LeadNotFoundError(Exception):
    pass


class LeadConflictError(Exception):
    """Stale ``expected_version``; carries the current state for the 409 body."""

    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("version_conflict")
        self.current = current


class LeadTransitionError(Exception):
    def __init__(self, current: dict[str, Any], allowed: list[str]) -> None:
        super().__init__("invalid_transition")
        self.current = current
        self.allowed = allowed


class LeadTransaction(Protocol):
    async def lock_key(self, key: str) -> None: ...
    async def get_idempotency(self, tenant_id: UUID, key: str) -> dict[str, Any] | None: ...
    async def save_idempotency(
        self,
        *,
        tenant_id: UUID,
        key: str,
        request_hash: str,
        response: dict[str, Any],
        expires_at: datetime,
    ) -> None: ...
    async def insert_lead(self, row: dict[str, Any]) -> None: ...
    async def get_lead(self, lead_id: UUID, tenant_id: UUID | None) -> dict[str, Any] | None: ...
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
    async def add_chain_events(
        self, events: list[dict[str, Any]], *, tenant_id: UUID | None, aggregate_id: UUID
    ) -> None: ...


class LeadUnitOfWork(Protocol):
    async def __aenter__(self) -> LeadTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


def request_hash(payload: LeadCreate) -> str:
    dump = payload.model_dump(mode="json")
    if dump.get("scored_event_id") is None:
        dump.pop("scored_event_id", None)  # keeps the hash of keys stored before the field
    body = json.dumps(dump, sort_keys=True).encode()
    return hashlib.sha256(body).hexdigest()


def lead_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "lead_id": str(row["id"]),
        "stage": str(row["stage"]),
        "version": int(row["version"]),
    }


async def _stored_scored_event_id(tx: Any, company_id: UUID, tenant_id: UUID) -> UUID | None:
    """Read the signal.scored id core stored for the company (None when unknown)."""
    reader = getattr(tx, "scored_event_id", None)
    if reader is None:
        return None
    found: UUID | None = await reader(company_id, tenant_id)
    return found


async def create_lead(
    unit_of_work: LeadUnitOfWork,
    payload: LeadCreate,
    *,
    tenant_id: UUID,
    idempotency_key: str,
    request_id: str | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return ``(response, created)``; ``created`` is False for an idempotent replay."""
    moment = now or datetime.now(UTC)
    digest = request_hash(payload)
    async with unit_of_work as tx:
        await tx.lock_key(f"lead-idem:{tenant_id}:{idempotency_key}")
        stored = await tx.get_idempotency(tenant_id, idempotency_key)
        if stored is not None:
            if stored["request_hash"] != digest:
                raise IdempotencyKeyReuseError(idempotency_key)
            replay: dict[str, Any] = stored["response_body"]
            return replay, False
        lead_pk = uuid4()
        row: dict[str, Any] = {
            "id": lead_pk,
            "tenant_id": tenant_id,
            "company_id": payload.company_id,
            "service_line": payload.service_line,
            "city": payload.city,
            "source": payload.source,
            "stage": INITIAL_STAGE.value,
            "qualification_reason": payload.qualification_reason,
            "version": 1,
            "created_at": moment,
            "updated_at": moment,
        }
        await tx.insert_lead(row)
        response = lead_view(row)
        await tx.add_outbox_event(
            event_id=uuid4(),
            tenant_id=tenant_id,
            aggregate_id=lead_pk,
            version=1,
            payload={
                "lead_id": str(row["id"]),
                "company_id": str(payload.company_id),
                **lead_dimensions(row),
                "stage": row["stage"],
                "origin": "manual",
                "request_id": request_id,
            },
        )
        if get_settings().event_chain_emit:
            # company.matched -> signal.explained; the scored parent lives in the ML service.
            scored_event_id = payload.scored_event_id
            if scored_event_id is None:
                # Not part of the request hash: the stored id changes with each new score.
                scored_event_id = await _stored_scored_event_id(tx, payload.company_id, tenant_id)
            if scored_event_id is not None:
                parent = {
                    "event_id": str(scored_event_id),
                    "event_type": "signal.scored",
                    "source_version": "",
                }
            else:
                parent = reference_parent("signal.scored", str(payload.company_id))
            matched = chain_events_after(
                parent,
                [
                    (
                        "company.matched",
                        {"lead_id": str(lead_pk), "company_id": str(payload.company_id)},
                        CHAIN_PRODUCER_VERSION,
                    ),
                    (
                        "signal.explained",
                        {
                            "lead_id": str(lead_pk),
                            "company_id": str(payload.company_id),
                            "reason": payload.qualification_reason,
                        },
                        CHAIN_PRODUCER_VERSION,
                    ),
                ],
                enabled=True,
            )
            await tx.add_chain_events(matched, tenant_id=tenant_id, aggregate_id=lead_pk)
        await tx.save_idempotency(
            tenant_id=tenant_id,
            key=idempotency_key,
            request_hash=digest,
            response=response,
            expires_at=moment + IDEMPOTENCY_TTL,
        )
        return response, True


async def change_status(
    unit_of_work: LeadUnitOfWork,
    lead_id: UUID,
    patch: LeadStatusPatch,
    *,
    tenant_id: UUID | None = None,
    actor: str | None = None,
    now: datetime | None = None,
    on_hot: HotCallback | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Move a lead to ``patch.stage`` or raise a conflict or transition error."""
    moment = now or datetime.now(UTC)
    async with unit_of_work as tx:
        row = await tx.get_lead(lead_id, tenant_id)
        if row is None:
            raise LeadNotFoundError(str(lead_id))
        current = PipelineStage(row["stage"])
        try:
            validate_transition(
                current,
                patch.stage,
                expected_version=patch.expected_version,
                version=int(row["version"]),
            )
        except VersionConflictError as exc:
            raise LeadConflictError(lead_view(row)) from exc
        except InvalidTransitionError as exc:
            raise LeadTransitionError(lead_view(row), [s.value for s in exc.allowed]) from exc
        updated = {
            **row,
            "stage": patch.stage.value,
            "version": int(row["version"]) + 1,
            "updated_at": moment,
            "last_transition_at": moment,
        }
        await tx.update_stage(updated)
        await tx.add_audit(
            action="lead.status_changed",
            lead_id=lead_id,
            payload={
                "from": current.value,
                "to": patch.stage.value,
                "version": updated["version"],
                "actor": actor,
            },
        )
        view = lead_view(updated)
        if get_settings().event_chain_emit:
            # project.pipeline_updated -> project.forecast_updated; the gate parent lives in funnel.
            if patch.gate_event_id is not None:
                parent = {
                    "event_id": str(patch.gate_event_id),
                    "event_type": "funnel.gate_decision",
                    "source_version": "",
                }
            else:
                parent = reference_parent("funnel.gate_decision", str(lead_id))
            moved = chain_events_after(
                parent,
                [
                    (
                        "project.pipeline_updated",
                        {
                            "lead_id": str(lead_id),
                            "from": current.value,
                            "to": patch.stage.value,
                            "version": updated["version"],
                        },
                        CHAIN_PRODUCER_VERSION,
                    ),
                    (
                        "project.forecast_updated",
                        {"lead_id": str(lead_id), "stage": patch.stage.value},
                        CHAIN_PRODUCER_VERSION,
                    ),
                ],
                enabled=True,
            )
            await tx.add_chain_events(moved, tenant_id=row.get("tenant_id"), aggregate_id=lead_id)
    if updated["stage"] == PipelineStage.HOT.value:
        alert = HotAlert(
            tenant_id=row.get("tenant_id"),
            lead_id=lead_id,
            company_id=row["company_id"],
            version=int(updated["version"]),
            score=None,
            committed_at=clock(),
        )
        await fire(on_hot, [alert])
    return view
