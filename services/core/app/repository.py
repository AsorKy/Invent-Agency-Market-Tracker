"""Persist signals and basic reads for the Core service.

``create_signal`` generates a deterministic external key when the source does
not provide one and writes the signal plus outbox record in one transaction;
list methods query company and lead projections. It relates to ``main.py``,
``outbox_relay.py``, and ``libs/contracts/api.py``. It preserves idempotency on
ingest retries and turns accepted changes into events.
"""

import hashlib
import json
import sys
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager as AsyncContextManager
from contextlib import asynccontextmanager
from datetime import datetime
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from libs.common.db import create_engine
from libs.common.pagination import decode_cursor, encode_cursor
from libs.common.settings import get_settings
from libs.common.tenant import set_tenant_context
from libs.contracts.api import SignalCreate
from libs.domain.event_chain import chain_events_after, stamp_payload
from services.core.app.brief_facts import read_brief_facts
from services.core.app.event_dimensions import clean
from services.core.app.score_features import read_score_features
from services.core.app.score_rules import read_score_rules

CHAIN_PRODUCER_VERSION = "core-chain-v1"
_CHAIN_EVENT_BY_TENANT = """
    INSERT INTO core.outbox_event
        (event_id, tenant_id, aggregate_type, aggregate_id, event_type, version, payload)
    VALUES
        (:event_id, :tenant_id, :aggregate_type, :aggregate_id, :event_type, 1,
         CAST(:payload AS jsonb))
"""
_CHAIN_EVENT_BY_COMPANY = """
    INSERT INTO core.outbox_event
        (event_id, tenant_id, aggregate_type, aggregate_id, event_type, version, payload)
    VALUES
        (:event_id,
         (SELECT c.tenant_id FROM core.company c WHERE c.id = :company_id
          ORDER BY c.version DESC LIMIT 1),
         :aggregate_type, :aggregate_id, :event_type, 1, CAST(:payload AS jsonb))
"""


async def insert_chain_events(
    connection: AsyncConnection,
    events: list[dict[str, Any]],
    *,
    aggregate_type: str,
    aggregate_id: UUID,
    tenant_id: UUID | None = None,
    company_id: UUID | None = None,
) -> None:
    """Write chain events (``chain_events_after`` output) into the open transaction's outbox.

    The tenant comes from ``tenant_id`` or, when absent, from the company row (RLS context must
    already be set by the caller).
    """
    for event in events:
        params: dict[str, Any] = {
            "event_id": event["event_id"],
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "event_type": event["event_type"],
            "payload": json.dumps(event["payload"], default=str),
        }
        if tenant_id is not None or company_id is None:
            await connection.execute(
                text(_CHAIN_EVENT_BY_TENANT), params | {"tenant_id": tenant_id}
            )
        else:
            await connection.execute(
                text(_CHAIN_EVENT_BY_COMPANY), params | {"company_id": company_id}
            )


class SqlCompanyTransaction:
    """Company writes bound to one open connection (one transaction)."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def lock_key(self, key: str) -> None:
        await self.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"company:{key}"}
        )

    async def find_company(self, nit: str | None, slug: str) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT id, tenant_id, nit, slug, legal_name, trade_name, sector_code,
                       employee_count, website, city, source_flags, version, valid_from
                FROM core.company
                WHERE (CAST(:nit AS text) IS NOT NULL AND nit = :nit) OR slug = :slug
                ORDER BY (nit = :nit) DESC NULLS LAST
                LIMIT 1
                FOR UPDATE
                """
            ),
            {"nit": nit, "slug": slug},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def insert_company(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.company
                    (id, tenant_id, nit, slug, legal_name, trade_name, sector_code,
                     employee_count, website, city, source_flags, version, valid_from,
                     created_at, updated_at)
                VALUES
                    (:id, :tenant_id, :nit, :slug, :legal_name, :trade_name, :sector_code,
                     :employee_count, :website, :city, CAST(:source_flags AS jsonb), :version,
                     :valid_from, :updated_at, :updated_at)
                """
            ),
            {**row, "source_flags": json.dumps(row["source_flags"])},
        )

    async def close_version(self, old: dict[str, object], closed_at: datetime) -> None:
        """Append the superseded version to history (SCD2 row with valid_from/valid_to)."""
        snapshot = {k: v for k, v in old.items() if k not in {"id", "valid_from"}}
        await self.connection.execute(
            text(
                """
                INSERT INTO core.audit_log (actor, action, aggregate_type, aggregate_id, payload)
                VALUES ('core', 'company.version_closed', 'company', :id, CAST(:payload AS jsonb))
                """
            ),
            {
                "id": old["id"],
                "payload": json.dumps(
                    snapshot
                    | {
                        "valid_from": str(old["valid_from"]),
                        "valid_to": closed_at.isoformat(),
                    },
                    default=str,
                ),
            },
        )

    async def update_company(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                UPDATE core.company SET
                    nit = :nit, legal_name = :legal_name, trade_name = :trade_name,
                    sector_code = :sector_code, employee_count = :employee_count,
                    website = :website, city = :city,
                    source_flags = CAST(:source_flags AS jsonb), version = :version,
                    valid_from = :valid_from, updated_at = :updated_at
                WHERE id = :id
                """
            ),
            {**row, "source_flags": json.dumps(row["source_flags"])},
        )

    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_id: UUID,
        version: int,
        payload: dict[str, object],
    ) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.outbox_event
                    (event_id, tenant_id, aggregate_type, aggregate_id, event_type, version,
                     payload)
                VALUES
                    (:event_id, :tenant_id, 'company', :aggregate_id, 'company.updated', :version,
                     CAST(:payload AS jsonb))
                """
            ),
            {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "aggregate_id": aggregate_id,
                "version": version,
                "payload": json.dumps(payload, default=str),
            },
        )


class SqlCompanyUnitOfWork:
    """Open ``engine.begin()``: commit on success, roll back on any error."""

    def __init__(self, engine: AsyncEngine, tenant_id: UUID | None = None) -> None:
        self._context = engine.begin()
        self._tenant_id = tenant_id

    async def __aenter__(self) -> SqlCompanyTransaction:
        connection = await self._context.__aenter__()
        if self._tenant_id is not None:
            # Row level security on core.company checks app.tenant_id: set it before any read
            # or write so a tenant-owned company is visible and insertable (REQ-SEC tenancy).
            try:
                await set_tenant_context(connection, self._tenant_id)
            except BaseException:
                await self._context.__aexit__(*sys.exc_info())
                raise
        return SqlCompanyTransaction(connection)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._context.__aexit__(exc_type, exc, tb)


class SqlLeadTransaction:
    """Lead writes bound to one open connection (one transaction)."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def lock_key(self, key: str) -> None:
        await self.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key}
        )

    async def get_idempotency(self, tenant_id: UUID, key: str) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT request_hash, response_body FROM core.idempotency_key
                WHERE tenant_id = :tenant_id AND idempotency_key = :key
                  AND status = 'completed' AND expires_at > now()
                """
            ),
            {"tenant_id": tenant_id, "key": key},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def save_idempotency(
        self,
        *,
        tenant_id: UUID,
        key: str,
        request_hash: str,
        response: dict[str, object],
        expires_at: datetime,
    ) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.idempotency_key
                    (tenant_id, idempotency_key, request_hash, status, response_code,
                     response_body, completed_at, expires_at)
                VALUES
                    (:tenant_id, :key, :request_hash, 'completed', 201,
                     CAST(:response AS jsonb), now(), :expires_at)
                ON CONFLICT (tenant_id, idempotency_key) DO UPDATE SET
                    request_hash = EXCLUDED.request_hash, status = 'completed',
                    response_code = 201, response_body = EXCLUDED.response_body,
                    completed_at = now(), created_at = now(), expires_at = EXCLUDED.expires_at
                """
            ),
            {
                "tenant_id": tenant_id,
                "key": key,
                "request_hash": request_hash,
                "response": json.dumps(response),
                "expires_at": expires_at,
            },
        )

    async def insert_lead(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.lead
                    (id, tenant_id, company_id, service_line, city, source, stage,
                     qualification_reason, version, created_at, updated_at)
                VALUES
                    (:id, :tenant_id, :company_id, :service_line, :city, :source, :stage,
                     CAST(:qualification_reason AS jsonb), :version, :created_at, :updated_at)
                """
            ),
            {**row, "qualification_reason": json.dumps(row["qualification_reason"])},
        )

    async def get_lead(self, lead_id: UUID, tenant_id: UUID | None) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT id, tenant_id, company_id, service_line, city, stage, version
                FROM core.lead
                WHERE id = :id AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = :tenant_id)
                FOR UPDATE
                """
            ),
            {"id": lead_id, "tenant_id": tenant_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def update_stage(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                UPDATE core.lead SET stage = :stage, version = :version,
                    updated_at = :updated_at, last_transition_at = :last_transition_at
                WHERE id = :id
                """
            ),
            row,
        )

    async def add_audit(self, *, action: str, lead_id: UUID, payload: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.audit_log (actor, action, aggregate_type, aggregate_id, payload)
                VALUES ('core', :action, 'lead', :id, CAST(:payload AS jsonb))
                """
            ),
            {"action": action, "id": lead_id, "payload": json.dumps(payload, default=str)},
        )

    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_id: UUID,
        version: int,
        payload: dict[str, object],
    ) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.outbox_event
                    (event_id, tenant_id, aggregate_type, aggregate_id, event_type, version,
                     payload)
                VALUES
                    (:event_id, :tenant_id, 'lead', :aggregate_id, 'lead.created', :version,
                     CAST(:payload AS jsonb))
                """
            ),
            {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "aggregate_id": aggregate_id,
                "version": version,
                "payload": json.dumps(payload, default=str),
            },
        )

    async def add_chain_events(
        self,
        events: list[dict[str, Any]],
        *,
        tenant_id: UUID | None,
        aggregate_id: UUID,
    ) -> None:
        await insert_chain_events(
            self.connection,
            events,
            aggregate_type="lead",
            aggregate_id=aggregate_id,
            tenant_id=tenant_id,
        )


class SqlActionTransaction:
    """Outcome, activity, and project writes bound to one open connection."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def get_lead(self, lead_id: UUID, tenant_id: UUID | None) -> dict[str, object] | None:
        return await SqlLeadTransaction(self.connection).get_lead(lead_id, tenant_id)

    async def insert_outcome(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.lead_outcome (id, tenant_id, lead_id, outcome, notes, occurred_at)
                VALUES (:id, :tenant_id, :lead_id, :outcome, :notes, :occurred_at)
                """
            ),
            row,
        )

    async def find_activity(self, tenant_id: UUID, key: str) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT id FROM core.lead_activity
                WHERE tenant_id = :tenant_id AND idempotency_key = :key
                """
            ),
            {"tenant_id": tenant_id, "key": key},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def insert_activity(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.lead_activity
                    (id, tenant_id, lead_id, activity_type, channel, source, payload,
                     occurred_at, created_by, idempotency_key)
                VALUES
                    (:id, :tenant_id, :lead_id, :activity_type, :channel, :source,
                     CAST(:payload AS jsonb), :occurred_at, :created_by, :idempotency_key)
                """
            ),
            {**row, "payload": json.dumps(row["payload"], default=str)},
        )

    async def find_project_for_lead(self, lead_id: UUID) -> UUID | None:
        result = await self.connection.execute(
            text("SELECT id FROM core.project WHERE lead_id = :lead_id LIMIT 1"),
            {"lead_id": lead_id},
        )
        found: UUID | None = result.scalar_one_or_none()
        return found

    async def insert_project(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.project
                    (id, tenant_id, lead_id, name, service_line, hypothesis, status,
                     estimated_roi, metadata, created_at, updated_at)
                VALUES
                    (:id, :tenant_id, :lead_id, :name, :service_line, :hypothesis, :status,
                     :estimated_roi, CAST(:metadata AS jsonb), :created_at, :updated_at)
                """
            ),
            {**row, "metadata": json.dumps(row["metadata"])},
        )
        # T-181 (D-26): the conversion fills core.lead.project_id in the same transaction.
        await self.connection.execute(
            text(
                """
                UPDATE core.lead SET project_id = :project_id
                WHERE id = :lead_id AND tenant_id = :tenant_id AND project_id IS NULL
                """
            ),
            {
                "project_id": row["id"],
                "lead_id": row.get("lead_id"),
                "tenant_id": row.get("tenant_id"),
            },
        )

    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.outbox_event
                    (event_id, tenant_id, aggregate_type, aggregate_id, event_type, version,
                     payload)
                VALUES
                    (:event_id, :tenant_id, :aggregate_type, :aggregate_id, :event_type, 1,
                     CAST(:payload AS jsonb))
                """
            ),
            {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "event_type": event_type,
                "payload": json.dumps(payload, default=str),
            },
        )


class SqlActionUnitOfWork(SqlCompanyUnitOfWork):
    """Same commit-or-rollback boundary, yielding action operations."""

    async def __aenter__(self) -> SqlActionTransaction:  # type: ignore[override]
        # ``super()`` sets the tenant context; opening the context directly would skip it.
        return SqlActionTransaction((await super().__aenter__()).connection)


class SqlFunnelTransaction(SqlActionTransaction):
    """Project lookup and gate-decision audit bound to one open connection."""

    async def get_project(
        self, project_id: UUID, tenant_id: UUID | None
    ) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT id, tenant_id FROM core.project
                WHERE id = :id AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = :tenant_id)
                """
            ),
            {"id": project_id, "tenant_id": tenant_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def add_audit(
        self,
        *,
        action: str,
        project_id: UUID,
        tenant_id: UUID | None,
        payload: dict[str, object],
    ) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.audit_log
                    (actor, action, aggregate_type, aggregate_id, tenant_id, payload)
                VALUES ('core', :action, 'project', :id, :tenant_id, CAST(:payload AS jsonb))
                """
            ),
            {
                "action": action,
                "id": project_id,
                "tenant_id": tenant_id,
                "payload": json.dumps(payload, default=str),
            },
        )


class SqlFunnelUnitOfWork(SqlCompanyUnitOfWork):
    """Same commit-or-rollback boundary, yielding funnel operations."""

    async def __aenter__(self) -> SqlFunnelTransaction:  # type: ignore[override]
        return SqlFunnelTransaction((await super().__aenter__()).connection)


class SqlValidationInputTransaction(SqlFunnelTransaction):
    """Strategist validation inputs of one project: one locked read, one write (T-179)."""

    async def get_inputs(self, project_id: UUID, tenant_id: UUID) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT id, COALESCE(metadata->'validation', '{}'::jsonb) AS inputs
                FROM core.project
                WHERE id = :id AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {"id": project_id, "tenant_id": tenant_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def set_inputs(
        self, project_id: UUID, tenant_id: UUID, inputs: dict[str, object]
    ) -> None:
        await self.connection.execute(
            text(
                """
                UPDATE core.project
                SET metadata = jsonb_set(metadata, '{validation}', CAST(:inputs AS jsonb), true),
                    updated_at = now()
                WHERE id = :id AND tenant_id = :tenant_id
                """
            ),
            {"id": project_id, "tenant_id": tenant_id, "inputs": json.dumps(inputs)},
        )


class SqlValidationInputUnitOfWork(SqlFunnelUnitOfWork):
    """Same boundary; the tenant context is set before the first statement."""

    async def __aenter__(self) -> SqlValidationInputTransaction:  # type: ignore[override]
        return SqlValidationInputTransaction((await super().__aenter__()).connection)


class SqlConfigTransaction:
    """Tenant configuration writes bound to one open connection."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def get_config(self, tenant_id: UUID) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT tenant_id, version, scoring_rules, brief_rules, bright_data_budget_usd,
                       metadata, updated_by, updated_at
                FROM core.tenant_config
                WHERE tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {"tenant_id": tenant_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    @staticmethod
    def _params(row: dict[str, object]) -> dict[str, object]:
        return {
            **row,
            "scoring_rules": json.dumps(row["scoring_rules"]),
            "brief_rules": json.dumps(row["brief_rules"]),
            "metadata": json.dumps(row["metadata"]),
        }

    async def insert_config(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.tenant_config
                    (tenant_id, version, scoring_rules, brief_rules, bright_data_budget_usd,
                     metadata, updated_by, created_at, updated_at)
                VALUES
                    (:tenant_id, :version, CAST(:scoring_rules AS jsonb),
                     CAST(:brief_rules AS jsonb), :bright_data_budget_usd,
                     CAST(:metadata AS jsonb), :updated_by, :updated_at, :updated_at)
                """
            ),
            self._params(row),
        )

    async def close_version(self, old: dict[str, object], closed_at: datetime) -> None:
        """Copy the superseded version into history (valid_from = its last update)."""
        await self.connection.execute(
            text(
                """
                INSERT INTO core.tenant_config_history
                    (tenant_id, version, scoring_rules, brief_rules, bright_data_budget_usd,
                     metadata, changed_by, valid_from, valid_to)
                VALUES
                    (:tenant_id, :version, CAST(:scoring_rules AS jsonb),
                     CAST(:brief_rules AS jsonb), :bright_data_budget_usd,
                     CAST(:metadata AS jsonb), :updated_by, :updated_at, :closed_at)
                """
            ),
            {**self._params(old), "closed_at": closed_at},
        )

    async def update_config(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                UPDATE core.tenant_config SET
                    version = :version, scoring_rules = CAST(:scoring_rules AS jsonb),
                    brief_rules = CAST(:brief_rules AS jsonb),
                    bright_data_budget_usd = :bright_data_budget_usd,
                    metadata = CAST(:metadata AS jsonb), updated_by = :updated_by,
                    updated_at = :updated_at
                WHERE tenant_id = :tenant_id
                """
            ),
            self._params(row),
        )

    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_id: UUID,
        version: int,
        payload: dict[str, object],
    ) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.outbox_event
                    (event_id, tenant_id, aggregate_type, aggregate_id, event_type, version,
                     payload)
                VALUES
                    (:event_id, :tenant_id, 'tenant', :aggregate_id, 'tenant.config_updated',
                     :version, CAST(:payload AS jsonb))
                """
            ),
            {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "aggregate_id": aggregate_id,
                "version": version,
                "payload": json.dumps(payload, default=str),
            },
        )


class SqlConfigUnitOfWork(SqlCompanyUnitOfWork):
    """Same commit-or-rollback boundary, yielding tenant configuration operations."""

    async def __aenter__(self) -> SqlConfigTransaction:  # type: ignore[override]
        return SqlConfigTransaction(await self._context.__aenter__())


class SqlQualificationTransaction(SqlLeadTransaction):
    """Inbox, lookups, and lead writes for automatic qualification."""

    async def set_tenant_context(self, tenant_id: UUID) -> None:
        await set_tenant_context(self.connection, tenant_id)

    async def mark_processed(self, event_id: UUID) -> bool:
        result = await self.connection.execute(
            text(
                """
                INSERT INTO core.processed_event (event_id) VALUES (:event_id)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
                """
            ),
            {"event_id": event_id},
        )
        return result.scalar_one_or_none() is not None

    async def get_company_tenant(self, company_id: UUID) -> tuple[bool, UUID | None]:
        result = await self.connection.execute(
            text("SELECT tenant_id FROM core.company WHERE id = :id"), {"id": company_id}
        )
        row = result.first()
        return (row is not None, row[0] if row else None)

    async def get_config(self, tenant_id: UUID) -> dict[str, object] | None:
        return await SqlConfigTransaction(self.connection).get_config(tenant_id)

    async def find_leads(self, company_id: UUID) -> list[dict[str, object]]:
        result = await self.connection.execute(
            text(
                """
                SELECT id, tenant_id, company_id, service_line, stage, version
                FROM core.lead WHERE company_id = :company_id
                ORDER BY created_at, id
                FOR UPDATE
                """
            ),
            {"company_id": company_id},
        )
        return [dict(row) for row in result.mappings().all()]

    async def has_manual_transition(self, lead_id: UUID) -> bool:
        result = await self.connection.execute(
            text(
                """
                SELECT 1 FROM core.audit_log
                WHERE aggregate_type = 'lead' AND aggregate_id = :id
                  AND action = 'lead.status_changed'
                LIMIT 1
                """
            ),
            {"id": lead_id},
        )
        return result.first() is not None


class SqlQualificationUnitOfWork(SqlCompanyUnitOfWork):
    """Same commit-or-rollback boundary, yielding qualification operations."""

    async def __aenter__(self) -> SqlQualificationTransaction:  # type: ignore[override]
        return SqlQualificationTransaction(await self._context.__aenter__())


class SqlOutboxTransaction:
    """Outbox claim, publish bookkeeping, and dead lettering on one connection."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def claim_due(self, now: datetime, limit: int) -> list[dict[str, object]]:
        """Due, undelivered events; an aggregate with an earlier waiting event is held back."""
        result = await self.connection.execute(
            text(
                """
                SELECT e.id, e.event_id, e.aggregate_type, e.aggregate_id, e.event_type,
                       e.version, e.payload, e.attempts, e.replay, e.tenant_id
                FROM core.outbox_event e
                WHERE e.published_at IS NULL AND e.dead_lettered_at IS NULL
                  AND (e.next_attempt_at IS NULL OR e.next_attempt_at <= :now)
                  AND NOT EXISTS (
                      SELECT 1 FROM core.outbox_event p
                      WHERE p.aggregate_id = e.aggregate_id AND p.id < e.id
                        AND p.published_at IS NULL AND p.dead_lettered_at IS NULL
                        AND p.next_attempt_at > :now
                  )
                ORDER BY e.id
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"now": now, "limit": limit},
        )
        return [dict(row) for row in result.mappings().all()]

    async def mark_published(self, row_id: int, now: datetime) -> None:
        await self.connection.execute(
            text("UPDATE core.outbox_event SET published_at = :now WHERE id = :id"),
            {"id": row_id, "now": now},
        )

    async def record_failure(
        self, row_id: int, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None:
        await self.connection.execute(
            text(
                """
                UPDATE core.outbox_event
                SET attempts = :attempts, next_attempt_at = :next_attempt_at, last_error = :error
                WHERE id = :id
                """
            ),
            {
                "id": row_id,
                "attempts": attempts,
                "next_attempt_at": next_attempt_at,
                "error": error,
            },
        )

    async def dead_letter(
        self, row: dict[str, object], *, attempts: int, error: str, now: datetime
    ) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.outbox_dead_letter
                    (event_id, tenant_id, event_type, payload, error, attempts, source_outbox_id,
                     replay)
                VALUES
                    (:event_id, :tenant_id, :event_type, CAST(:payload AS jsonb), :error,
                     :attempts,
                     :id, COALESCE(:replay, false))
                """
            ),
            {
                "event_id": row["event_id"],
                "tenant_id": row.get("tenant_id"),
                "event_type": row["event_type"],
                "payload": json.dumps(row["payload"], default=str),
                "error": error,
                "attempts": attempts,
                "id": row["id"],
                "replay": row.get("replay"),
            },
        )
        await self.connection.execute(
            text(
                """
                UPDATE core.outbox_event
                SET attempts = :attempts, last_error = :error, dead_lettered_at = :now
                WHERE id = :id
                """
            ),
            {"id": row["id"], "attempts": attempts, "error": error, "now": now},
        )


class SqlOutboxUnitOfWork(SqlCompanyUnitOfWork):
    """Same commit-or-rollback boundary, yielding outbox operations."""

    async def __aenter__(self) -> SqlOutboxTransaction:  # type: ignore[override]
        return SqlOutboxTransaction(await self._context.__aenter__())


class SqlImportTransaction:
    """Import jobs, blobs, and rows on one connection; writers share the same connection."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection
        self.company_tx = SqlCompanyTransaction(connection)
        self.lead_tx = SqlLeadTransaction(connection)
        self.action_tx = SqlActionTransaction(connection)

    def row_scope(self) -> AsyncContextManager[None]:
        """Savepoint so one failing row does not abort the chunk transaction."""
        return self._savepoint()

    @asynccontextmanager
    async def _savepoint(self) -> AsyncIterator[None]:
        async with self.connection.begin_nested():
            yield

    async def lock_key(self, key: str) -> None:
        await self.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key}
        )

    async def find_job_by_key(self, tenant_id: UUID, key: str) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT j.job_id, j.entity, j.status, j.total_rows, j.processed_rows,
                       j.upserted_rows, j.duplicate_rows, j.error_rows, j.error_message,
                       j.started_at, j.finished_at, b.sha256
                FROM core.import_job j JOIN core.upload_blob b ON b.upload_id = j.upload_id
                WHERE j.tenant_id = :tenant_id AND j.idempotency_key = :key
                """
            ),
            {"tenant_id": tenant_id, "key": key},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def count_active_jobs(self, tenant_id: UUID) -> int:
        result = await self.connection.execute(
            text(
                """
                SELECT count(*) FROM core.import_job
                WHERE tenant_id = :tenant_id AND status IN ('queued', 'running')
                """
            ),
            {"tenant_id": tenant_id},
        )
        return int(result.scalar_one())

    async def insert_upload_and_job(self, row: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.upload_blob
                    (upload_id, tenant_id, original_filename, content_type, content, sha256)
                VALUES (:upload_id, :tenant_id, :filename, :content_type, :content, :sha256)
                """
            ),
            row,
        )
        await self.connection.execute(
            text(
                """
                INSERT INTO core.import_job
                    (job_id, tenant_id, upload_id, entity, idempotency_key, total_rows,
                     preserve_blob)
                VALUES
                    (:job_id, :tenant_id, :upload_id, :entity, :idempotency_key, :total_rows,
                     :preserve_blob)
                """
            ),
            row,
        )

    async def get_job(self, tenant_id: UUID | None, job_id: UUID) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                SELECT job_id, entity, status, total_rows, processed_rows, upserted_rows,
                       duplicate_rows, error_rows, error_message, started_at, finished_at
                FROM core.import_job
                WHERE job_id = :job_id
                  AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = :tenant_id)
                """
            ),
            {"job_id": job_id, "tenant_id": tenant_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def claim_next_job(self, now: datetime) -> dict[str, object] | None:
        result = await self.connection.execute(
            text(
                """
                UPDATE core.import_job SET status = 'running', started_at = :now
                WHERE job_id = (
                    SELECT job_id FROM core.import_job WHERE status = 'queued'
                    ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
                )
                RETURNING job_id, tenant_id, upload_id, entity, preserve_blob
                """
            ),
            {"now": now},
        )
        job = result.mappings().first()
        if job is None:
            return None
        blob = await self.connection.execute(
            text("SELECT content FROM core.upload_blob WHERE upload_id = :id"),
            {"id": job["upload_id"]},
        )
        return {**dict(job), "content": bytes(blob.scalar_one())}

    async def insert_row(
        self, job: dict[str, object], row_number: int, sha: str, payload: dict[str, object]
    ) -> bool:
        result = await self.connection.execute(
            text(
                """
                INSERT INTO core.import_row
                    (tenant_id, job_id, entity, row_number, row_sha256, payload)
                VALUES
                    (:tenant_id, :job_id, :entity, :row_number, :sha, CAST(:payload AS jsonb))
                ON CONFLICT (tenant_id, entity, row_sha256) DO NOTHING
                RETURNING id
                """
            ),
            {
                "tenant_id": job["tenant_id"],
                "job_id": job["job_id"],
                "entity": job["entity"],
                "row_number": row_number,
                "sha": sha,
                "payload": json.dumps(payload),
            },
        )
        return result.scalar_one_or_none() is not None

    async def set_row_status(self, job_id: UUID, row_number: int, status: str) -> None:
        await self.connection.execute(
            text(
                "UPDATE core.import_row SET status = :status "
                "WHERE job_id = :job_id AND row_number = :row_number"
            ),
            {"status": status, "job_id": job_id, "row_number": row_number},
        )

    async def record_error_row(
        self,
        job: dict[str, object],
        row_number: int,
        sha: str,
        payload: dict[str, object],
        error: str,
    ) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.import_row
                    (tenant_id, job_id, entity, row_number, row_sha256, payload, status,
                     error_message)
                VALUES
                    (:tenant_id, :job_id, :entity, :row_number, :sha, CAST(:payload AS jsonb),
                     'error', :error)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "tenant_id": job["tenant_id"],
                "job_id": job["job_id"],
                "entity": job["entity"],
                "row_number": row_number,
                "sha": sha,
                "payload": json.dumps(payload),
                "error": error,
            },
        )

    async def add_progress(self, job_id: UUID, counters: dict[str, int]) -> None:
        await self.connection.execute(
            text(
                """
                UPDATE core.import_job SET
                    processed_rows = processed_rows + :processed,
                    upserted_rows = upserted_rows + :upserted,
                    duplicate_rows = duplicate_rows + :duplicates,
                    error_rows = error_rows + :errors
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id, **counters},
        )

    async def finish_job(
        self, job: dict[str, object], *, status: str, error: str | None, now: datetime
    ) -> None:
        await self.connection.execute(
            text(
                """
                UPDATE core.import_job
                SET status = :status, error_message = :error, finished_at = :now
                WHERE job_id = :job_id
                """
            ),
            {"status": status, "error": error, "now": now, "job_id": job["job_id"]},
        )
        if not job["preserve_blob"]:
            # import_job.upload_id is NOT NULL with a foreign key, so the row stays and the
            # content is emptied.
            await self.connection.execute(
                text("UPDATE core.upload_blob SET content = ''::bytea WHERE upload_id = :id"),
                {"id": job["upload_id"]},
            )


class SqlImportUnitOfWork(SqlCompanyUnitOfWork):
    """Same commit-or-rollback boundary, yielding import operations."""

    async def __aenter__(self) -> SqlImportTransaction:  # type: ignore[override]
        return SqlImportTransaction(await self._context.__aenter__())


class SqlRetentionTransaction:
    """Retention queries and cascading company deletion on one connection."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def find_purge_candidates(self, cutoff: datetime, limit: int) -> list[UUID]:
        result = await self.connection.execute(
            text(
                """
                SELECT c.id FROM core.company c
                WHERE COALESCE(
                          (SELECT max(s.observed_at) FROM core.signal s WHERE s.company_id = c.id),
                          c.created_at) < :cutoff
                ORDER BY c.created_at, c.id
                LIMIT :limit
                FOR UPDATE OF c SKIP LOCKED
                """
            ),
            {"cutoff": cutoff, "limit": limit},
        )
        return [row[0] for row in result.all()]

    async def find_company(self, company_id: UUID | None, nit: str | None) -> UUID | None:
        result = await self.connection.execute(
            text(
                """
                SELECT id FROM core.company
                WHERE (CAST(:company_id AS uuid) IS NOT NULL AND id = :company_id)
                   OR (CAST(:nit AS text) IS NOT NULL AND nit = :nit)
                LIMIT 1
                FOR UPDATE
                """
            ),
            {"company_id": company_id, "nit": nit},
        )
        found: UUID | None = result.scalar_one_or_none()
        return found

    async def delete_company_data(self, company_id: UUID) -> dict[str, int]:
        """Delete dependants first (outcomes, activities, leads, signals, roles, locations)."""
        params = {"id": company_id}
        statements = (
            (
                "lead_outcome",
                "DELETE FROM core.lead_outcome WHERE lead_id IN "
                "(SELECT id FROM core.lead WHERE company_id = :id)",
            ),
            (
                "lead_activity",
                "DELETE FROM core.lead_activity WHERE lead_id IN "
                "(SELECT id FROM core.lead WHERE company_id = :id)",
            ),
            ("lead", "DELETE FROM core.lead WHERE company_id = :id"),
            ("signal", "DELETE FROM core.signal WHERE company_id = :id"),
            ("person_role", "DELETE FROM core.person_role WHERE company_id = :id"),
            ("location", "DELETE FROM core.location WHERE company_id = :id"),
            (
                "audit_log",
                "DELETE FROM core.audit_log WHERE aggregate_type = 'company' "
                "AND aggregate_id = :id",
            ),
            ("company", "DELETE FROM core.company WHERE id = :id"),
        )
        counts: dict[str, int] = {}
        for table, statement in statements:
            result = await self.connection.execute(text(statement), params)
            counts[table] = int(result.rowcount)
        return counts

    async def add_audit(self, *, action: str, payload: dict[str, object]) -> None:
        await self.connection.execute(
            text(
                """
                INSERT INTO core.audit_log (actor, action, aggregate_type, payload)
                VALUES ('core', :action, 'compliance', CAST(:payload AS jsonb))
                """
            ),
            {"action": action, "payload": json.dumps(payload, default=str)},
        )


class SqlRetentionUnitOfWork(SqlCompanyUnitOfWork):
    """Same commit-or-rollback boundary, yielding retention operations."""

    async def __aenter__(self) -> SqlRetentionTransaction:  # type: ignore[override]
        return SqlRetentionTransaction((await super().__aenter__()).connection)


class SqlLeadUnitOfWork(SqlCompanyUnitOfWork):
    """Same commit-or-rollback boundary, yielding lead operations."""

    async def __aenter__(self) -> SqlLeadTransaction:  # type: ignore[override]
        return SqlLeadTransaction(await self._context.__aenter__())


class CoreRepository:
    def __init__(self, database_url: str | None) -> None:
        self.engine = create_engine(database_url, pool_size=4)

    def company_unit_of_work(self, tenant_id: UUID | None = None) -> SqlCompanyUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        return SqlCompanyUnitOfWork(self.engine, tenant_id)

    def lead_unit_of_work(self) -> SqlLeadUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        return SqlLeadUnitOfWork(self.engine)

    def action_unit_of_work(self, tenant_id: UUID | None = None) -> SqlActionUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        # core.lead and core.lead_outcome are under row level security: the tenant context
        # must be set before the first read or write (REQ-SEC-005, T-082).
        return SqlActionUnitOfWork(self.engine, tenant_id)

    def validation_input_unit_of_work(self, tenant_id: UUID) -> SqlValidationInputUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        return SqlValidationInputUnitOfWork(self.engine, tenant_id)

    def config_unit_of_work(self) -> SqlConfigUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        return SqlConfigUnitOfWork(self.engine)

    def qualification_unit_of_work(self) -> SqlQualificationUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        return SqlQualificationUnitOfWork(self.engine)

    def outbox_unit_of_work(self) -> SqlOutboxUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        return SqlOutboxUnitOfWork(self.engine)

    async def list_outbox_events_since(
        self, since: datetime, company_id: UUID | None, after_id: int, limit: int
    ) -> list[dict[str, object]]:
        """Page the append-only outbox log in id order for republishing."""
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core reads")
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, event_id, tenant_id, aggregate_type, aggregate_id, event_type,
                           version, payload
                    FROM core.outbox_event
                    WHERE created_at >= :since AND id > :after_id
                      AND (CAST(:company_id AS text) IS NULL
                           OR payload->>'company_id' = CAST(:company_id AS text))
                    ORDER BY id
                    LIMIT :limit
                    """
                ),
                {
                    "since": since,
                    "after_id": after_id,
                    "company_id": str(company_id) if company_id else None,
                    "limit": limit,
                },
            )
            return [dict(row) for row in result.mappings().all()]

    def import_unit_of_work(self) -> SqlImportUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        return SqlImportUnitOfWork(self.engine)

    def retention_unit_of_work(self) -> SqlRetentionUnitOfWork:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        return SqlRetentionUnitOfWork(self.engine)

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()

    async def create_signal(
        self, signal: SignalCreate, *, request_id: str | None
    ) -> tuple[UUID, UUID | None]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        signal_id = uuid4()
        event_id = uuid4()
        source_external_id = signal.source_external_id or self._derived_external_id(signal)
        payload = signal.model_dump(mode="json", exclude={"tenant_id"}) | {
            "signal_id": str(signal_id),
            "source_external_id": source_external_id,
        }
        emit_chain = get_settings().event_chain_emit
        if emit_chain:
            payload = stamp_payload(payload, "signal.created", CHAIN_PRODUCER_VERSION)
        async with self.engine.begin() as connection:
            if signal.tenant_id is not None:
                # The event tenant is read from core.company, which row level security hides
                # unless app.tenant_id matches.
                await set_tenant_context(connection, signal.tenant_id)
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO core.signal
                        (id, tenant_id, company_id, source, source_external_id, signal_type,
                         observed_at, strength, payload)
                    VALUES
                        (:id,
                         COALESCE(CAST(:tenant_id AS uuid),
                                  (SELECT c.tenant_id FROM core.company c
                                   WHERE c.id = :company_id ORDER BY c.version DESC LIMIT 1)),
                         :company_id, :source, :source_external_id, :signal_type,
                         :observed_at, :strength, CAST(:payload AS jsonb))
                    ON CONFLICT (tenant_id, source, source_external_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": signal_id,
                    "tenant_id": signal.tenant_id,
                    "company_id": signal.company_id,
                    "source": signal.source,
                    "source_external_id": source_external_id,
                    "signal_type": signal.signal_type,
                    "observed_at": signal.observed_at,
                    "strength": signal.strength,
                    "payload": json.dumps(signal.payload),
                },
            )
            inserted_signal_id = inserted.scalar_one_or_none()
            if inserted_signal_id is None:
                existing = await connection.execute(
                    text(
                        """
                        SELECT id FROM core.signal
                        WHERE source = :source AND source_external_id = :source_external_id
                          AND tenant_id = COALESCE(
                              CAST(:tenant_id AS uuid),
                              (SELECT c.tenant_id FROM core.company c
                               WHERE c.id = :company_id ORDER BY c.version DESC LIMIT 1))
                        """
                    ),
                    {
                        "tenant_id": signal.tenant_id,
                        "company_id": signal.company_id,
                        "source": signal.source,
                        "source_external_id": source_external_id,
                    },
                )
                existing_signal_id: UUID = existing.scalar_one()
                return existing_signal_id, None
            await connection.execute(
                text(
                    """
                    INSERT INTO core.outbox_event
                        (event_id, tenant_id, aggregate_type, aggregate_id, event_type, version,
                     payload)
                    VALUES
                        (:event_id,
                         (SELECT c.tenant_id FROM core.company c WHERE c.id = :company_id
                          ORDER BY c.version DESC LIMIT 1),
                         'signal', :aggregate_id, 'signal.created', 1,
                         CAST(:payload AS jsonb) || jsonb_strip_nulls(jsonb_build_object(
                             'city',
                             (SELECT NULLIF(btrim(c.city), '') FROM core.company c
                              WHERE c.id = :company_id ORDER BY c.version DESC LIMIT 1),
                             'service_line',
                             COALESCE(
                                 CAST(:service_line AS text),
                                 (SELECT l.service_line FROM core.lead l
                                  WHERE l.company_id = :company_id
                                  ORDER BY l.created_at DESC, l.id DESC LIMIT 1))))
                        )
                    """
                ),
                {
                    "event_id": event_id,
                    "company_id": signal.company_id,
                    "aggregate_id": signal_id,
                    "payload": json.dumps(payload | {"request_id": request_id}),
                    "service_line": clean(signal.payload.get("service_line")),
                },
            )
            if emit_chain:
                created = {
                    "event_id": str(event_id),
                    "event_type": "signal.created",
                    "source_version": payload["source_version"],
                }
                tagged = chain_events_after(
                    created,
                    [
                        (
                            "signal.tagged",
                            {
                                "signal_id": str(signal_id),
                                "company_id": str(signal.company_id),
                                "tags": {
                                    "source": signal.source,
                                    "signal_type": signal.signal_type,
                                },
                            },
                            CHAIN_PRODUCER_VERSION,
                        )
                    ],
                    enabled=True,
                )
                await insert_chain_events(
                    connection,
                    tagged,
                    aggregate_type="signal",
                    aggregate_id=signal_id,
                    company_id=signal.company_id,
                )
        return inserted_signal_id, event_id

    @staticmethod
    def _derived_external_id(signal: SignalCreate) -> str:
        fingerprint = json.dumps(
            {
                "company_id": str(signal.company_id),
                "source": signal.source,
                "signal_type": signal.signal_type,
                "observed_at": signal.observed_at.isoformat(),
                "strength": signal.strength,
                "payload": signal.payload,
            },
            sort_keys=True,
            default=str,
        ).encode()
        return hashlib.sha256(fingerprint).hexdigest()

    async def list_companies(
        self, limit: int, cursor: str | None = None
    ) -> tuple[list[dict[str, object]], str | None]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core reads")
        page_cursor = decode_cursor(cursor) if cursor else None
        cursor_filter = ""
        parameters: dict[str, object] = {"limit": limit + 1}
        if page_cursor:
            cursor_filter = "WHERE (updated_at, id) < (:cursor_updated_at, :cursor_id)"
            parameters.update(
                {"cursor_updated_at": page_cursor.updated_at, "cursor_id": page_cursor.item_id}
            )
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT id, slug, legal_name, trade_name, sector_code, employee_count,
                           website, city, version, updated_at
                    FROM core.company
                    {cursor_filter}
                    ORDER BY updated_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                parameters,
            )
            rows = [dict(row) for row in result.mappings().all()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            updated_at, item_id = last["updated_at"], last["id"]
            if not isinstance(updated_at, datetime) or not isinstance(item_id, UUID):
                raise RuntimeError("database returned invalid company cursor fields")
            next_cursor = encode_cursor(updated_at, item_id)
        return rows, next_cursor

    async def list_leads(
        self,
        limit: int,
        cursor: str | None = None,
        *,
        stage: str | None = None,
        service_line: str | None = None,
        city: str | None = None,
        segment: str | None = None,
        min_score: int | None = None,
        tenant_id: UUID | None = None,
    ) -> tuple[list[dict[str, object]], str | None]:
        """List leads newest first; ``segment`` is the company sector code."""
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core reads")
        page_cursor = decode_cursor(cursor) if cursor else None
        clauses: list[str] = []
        parameters: dict[str, object] = {"limit": limit + 1}
        filters: dict[str, tuple[object, str]] = {
            "stage": (stage, "l.stage = :stage"),
            "service_line": (service_line, "l.service_line = :service_line"),
            "city": (city, "l.city = :city"),
            "segment": (segment, "c.sector_code = :segment"),
            "min_score": (min_score, "c.score >= :min_score"),
            "tenant_id": (tenant_id, "l.tenant_id = :tenant_id"),
        }
        for name, (value, clause) in filters.items():
            if value is not None:
                clauses.append(clause)
                parameters[name] = value
        if page_cursor:
            clauses.append("(l.updated_at, l.id) < (:cursor_updated_at, :cursor_id)")
            parameters.update(
                {"cursor_updated_at": page_cursor.updated_at, "cursor_id": page_cursor.item_id}
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self.engine.connect() as connection:
            if tenant_id is not None:
                await set_tenant_context(connection, tenant_id)
            result = await connection.execute(
                text(
                    f"""
                    SELECT l.id, l.company_id, c.legal_name, l.service_line, l.stage, l.city,
                           c.sector_code AS segment, c.score, l.version,
                           l.created_at, l.updated_at
                    FROM core.lead l
                    JOIN core.company c ON c.id = l.company_id
                    {where}
                    ORDER BY l.updated_at DESC, l.id DESC
                    LIMIT :limit
                    """
                ),
                parameters,
            )
            rows = [dict(row) for row in result.mappings().all()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            updated_at, item_id = last["updated_at"], last["id"]
            if not isinstance(updated_at, datetime) or not isinstance(item_id, UUID):
                raise RuntimeError("database returned invalid lead cursor fields")
            next_cursor = encode_cursor(updated_at, item_id)
        return rows, next_cursor

    async def brief_facts(
        self, company_id: UUID, tenant_id: UUID | None = None
    ) -> dict[str, object] | None:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core reads")
        return await read_brief_facts(self.engine, company_id, tenant_id)

    async def store_company_score(
        self,
        *,
        company_id: UUID,
        tenant_id: UUID | None,
        score: int,
        source_version: str | None,
        now: datetime,
    ) -> bool:
        """Store the score of a ``lead.score_updated`` event; never lower a newer version.

        ``source_version`` is a non-negative integer as text. The row is written when nothing is
        stored yet, when the version is equal (a rescore with new weights) or when both versions
        are integers and the stored one is smaller. Anything else (older, or not comparable) is
        kept. Returns whether a row changed.
        """
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core writes")
        async with self.engine.begin() as connection:
            if tenant_id is not None:
                await set_tenant_context(connection, tenant_id)
            result = await connection.execute(
                text(
                    """
                    UPDATE core.company
                    SET score = :score, score_source_version = CAST(:source_version AS text),
                        score_updated_at = :now
                    WHERE id = :company_id AND tenant_id IS NOT DISTINCT FROM :tenant_id
                      AND (
                          score_source_version IS NULL
                          OR score_source_version = CAST(:source_version AS text)
                          OR CASE WHEN score_source_version ~ '^[0-9]{1,18}$'
                                   AND CAST(:source_version AS text) ~ '^[0-9]{1,18}$'
                                  THEN CAST(score_source_version AS bigint)
                                       < CAST(CAST(:source_version AS text) AS bigint)
                                  ELSE false END
                      )
                    """
                ),
                {
                    "company_id": company_id,
                    "tenant_id": tenant_id,
                    "score": score,
                    "source_version": source_version,
                    "now": now,
                },
            )
            return bool(result.rowcount)

    async def score_rules(self, tenant_id: UUID) -> dict[str, object]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core reads")
        return await read_score_rules(self.engine, tenant_id)

    async def score_features(
        self, tenant_id: UUID, limit: int, after: UUID | None
    ) -> dict[str, object]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core reads")
        return await read_score_features(self.engine, tenant_id, limit, after)

    async def get_lead(
        self, lead_id: UUID, tenant_id: UUID | None = None
    ) -> dict[str, object] | None:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core reads")
        async with self.engine.connect() as connection:
            if tenant_id is not None:
                await set_tenant_context(connection, tenant_id)
            result = await connection.execute(
                text(
                    """
                    SELECT l.id, l.company_id, c.legal_name, l.service_line, l.stage, l.city,
                           l.source, l.qualification_reason, c.sector_code AS segment,
                           c.score, l.version, l.last_transition_at,
                           l.created_at, l.updated_at
                    FROM core.lead l
                    JOIN core.company c ON c.id = l.company_id
                    WHERE l.id = :id AND (CAST(:tenant_id AS uuid) IS NULL
                                          OR l.tenant_id = :tenant_id)
                    """
                ),
                {"id": lead_id, "tenant_id": tenant_id},
            )
            row = result.mappings().first()
        return dict(row) if row else None

    async def list_projects(
        self,
        limit: int,
        cursor: str | None = None,
        *,
        status: str | None = None,
        tenant_id: UUID | None = None,
    ) -> tuple[list[dict[str, object]], str | None]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for core reads")
        page_cursor = decode_cursor(cursor) if cursor else None
        clauses: list[str] = []
        parameters: dict[str, object] = {"limit": limit + 1}
        if status is not None:
            clauses.append("status = :status")
            parameters["status"] = status
        if tenant_id is not None:
            clauses.append("tenant_id = :tenant_id")
            parameters["tenant_id"] = tenant_id
        if page_cursor:
            clauses.append("(updated_at, id) < (:cursor_updated_at, :cursor_id)")
            parameters.update(
                {"cursor_updated_at": page_cursor.updated_at, "cursor_id": page_cursor.item_id}
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT id, lead_id, name, service_line, hypothesis, status, estimated_roi,
                           metadata, created_at, updated_at
                    FROM core.project
                    {where}
                    ORDER BY updated_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                parameters,
            )
            rows = [dict(row) for row in result.mappings().all()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            updated_at, item_id = last["updated_at"], last["id"]
            if not isinstance(updated_at, datetime) or not isinstance(item_id, UUID):
                raise RuntimeError("database returned invalid project cursor fields")
            next_cursor = encode_cursor(updated_at, item_id)
        return rows, next_cursor
