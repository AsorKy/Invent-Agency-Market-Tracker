"""Deliver resolved snapshot records to core (REQ-ING-009).

For each record of a changed snapshot, ``SnapshotFlow`` applies the source
mapping file (``mapping.py``), registers the entity candidate, resolves it with
the injected resolver (``libs/domain/entity_resolution.py``), and then calls
core's internal API through an injectable ``CoreClient``: first the company
upsert, then the signal. ``HttpCoreClient`` is the real adapter and sends the
service token. A record that fails mapping, resolution, or delivery is written
to ``ingest.failed_record`` through a ``FailureSink`` and never stops the rest
of the batch. ``SqlResolutionStore`` buffers the writes of the synchronous
``ResolutionStore`` port and flushes them inside the caller's transaction.
It relates to ``repository.py`` and ``processor.py``.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from libs.common.retry import retry_with_backoff
from libs.domain.entity_resolution import Candidate, Resolution
from services.ingest.app.mapping import (
    MappingError,
    NormalizedRecord,
    SourceMapping,
    load_mapping,
    normalize_record,
)

SKIP_STATUSES = frozenset({"manual_review", "quarantined"})
DELIVER_DECISIONS = frozenset({"matched", "new_company"})
NAME_FIELDS = ("name", "company_name")
REASON_LIMIT = 500


class CoreClient(Protocol):
    """Port over core's internal API; every call must be idempotent."""

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]: ...

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]: ...


class FailureSink(Protocol):
    """Port that stores a failed record with its reason."""

    async def record(
        self,
        *,
        source: str,
        snapshot_id: str,
        record_ref: str | None,
        reason: str,
        payload: dict[str, Any],
    ) -> None: ...


@dataclass(frozen=True)
class Registered:
    """A candidate row as stored: its id, attempts so far, and status."""

    id: str
    attempts: int = 0
    status: str = "pending"


Register = Callable[[str, str | None, str | None], Awaitable[Registered]]
Resolve = Callable[[Candidate], Resolution]


class HttpCoreClient:
    """Call core with the ``X-Service-Token`` header; raises on non-2xx answers."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )
        self._headers = {"X-Service-Token": service_token}

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(path, json=body, headers=self._headers)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/internal/core/companies/upsert", body)

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/internal/core/signals", body)

    async def aclose(self) -> None:
        await self._client.aclose()


class SqlFailureSink:
    """Write failed records to ``ingest.failed_record`` in their own transaction."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def record(
        self,
        *,
        source: str,
        snapshot_id: str,
        record_ref: str | None,
        reason: str,
        payload: dict[str, Any],
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ingest.failed_record
                        (source, snapshot_id, record_ref, reason, payload)
                    VALUES (:source, :snapshot_id, :record_ref, :reason, CAST(:payload AS jsonb))
                    """
                ),
                {
                    "source": source,
                    "snapshot_id": snapshot_id,
                    "record_ref": record_ref,
                    "reason": reason[:REASON_LIMIT],
                    "payload": json.dumps(payload, default=str),
                },
            )


@dataclass
class SqlResolutionStore:
    """``ResolutionStore`` that buffers its writes; ``flush`` runs them in one transaction."""

    operations: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def log_match(
        self, candidate_id: str, rues_nit: str | None, score: float, decision: str
    ) -> None:
        self.operations.append(
            (
                "INSERT INTO ingest.entity_match_log (candidate_id, rues_nit, score, decision) "
                "VALUES (CAST(:candidate_id AS uuid), :rues_nit, :score, :decision)",
                {
                    "candidate_id": candidate_id,
                    "rues_nit": rues_nit,
                    "score": score,
                    "decision": decision,
                },
            )
        )

    def set_candidate_state(self, candidate_id: str, status: str, attempts: int) -> None:
        self.operations.append(
            (
                "UPDATE ingest.entity_candidate SET status = :status, "
                "resolution_attempts = :attempts WHERE id = CAST(:candidate_id AS uuid)",
                {"candidate_id": candidate_id, "status": status, "attempts": attempts},
            )
        )

    def enqueue_rues_lookup(
        self,
        candidate_id: str,
        attempts: int,
        next_attempt_at: datetime | None,
        last_error: str | None,
    ) -> None:
        self.operations.append(
            (
                "INSERT INTO ingest.rues_lookup_queue "
                "(candidate_id, status, attempts, next_attempt_at, last_error) "
                "VALUES (CAST(:candidate_id AS uuid), 'queued', :attempts, :next_attempt_at, "
                ":last_error) ON CONFLICT (candidate_id) DO UPDATE SET status = 'queued', "
                "attempts = EXCLUDED.attempts, next_attempt_at = EXCLUDED.next_attempt_at, "
                "last_error = EXCLUDED.last_error",
                {
                    "candidate_id": candidate_id,
                    "attempts": attempts,
                    "next_attempt_at": next_attempt_at,
                    "last_error": last_error,
                },
            )
        )

    def close_rues_lookup(self, candidate_id: str, status: str) -> None:
        self.operations.append(
            (
                "UPDATE ingest.rues_lookup_queue SET status = :status "
                "WHERE candidate_id = CAST(:candidate_id AS uuid)",
                {"candidate_id": candidate_id, "status": status},
            )
        )

    def quarantine(self, candidate_id: str, reason: str) -> None:
        self.operations.append(
            (
                "INSERT INTO ingest.quarantine (candidate_id, reason) "
                "VALUES (CAST(:candidate_id AS uuid), :reason)",
                {"candidate_id": candidate_id, "reason": reason[:REASON_LIMIT]},
            )
        )

    async def flush(self, connection: AsyncConnection) -> None:
        """Run and clear the buffered writes on ``connection``."""
        pending, self.operations = self.operations, []
        for statement, params in pending:
            await connection.execute(text(statement), params)


@dataclass(frozen=True)
class RecordOutcome:
    """Result of one record: ``delivered``, ``skipped``, ``pending`` or ``failed``."""

    status: str
    reason: str | None = None


def _json_safe(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


def _record_name(fields: dict[str, Any]) -> str | None:
    for name in NAME_FIELDS:
        value = fields.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class SnapshotFlow:
    """Map, resolve, and deliver records to core; never raises for one bad record."""

    def __init__(
        self,
        *,
        core: CoreClient,
        failures: FailureSink,
        resolve: Resolve,
        dataset_sources: Mapping[str, str],
        mappings: Mapping[str, SourceMapping] | None = None,
        retry_attempts: int = 2,
        retry_base_delay_seconds: float = 0.1,
        retry_max_delay_seconds: float = 2.0,
    ) -> None:
        self.core = core
        self.failures = failures
        self.resolve = resolve
        self.dataset_sources = dict(dataset_sources)
        self._mappings: dict[str, SourceMapping] = dict(mappings or {})
        self.retry_attempts = retry_attempts
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds

    def source_for_dataset(self, dataset_id: str) -> str | None:
        return self.dataset_sources.get(dataset_id)

    def _mapping(self, source: str) -> SourceMapping:
        if source not in self._mappings:
            self._mappings[source] = load_mapping(source)
        return self._mappings[source]

    def _external_ref(self, source: str, record: dict[str, Any]) -> str | None:
        for path in self._mapping(source).external_id:
            value = record.get(path)
            if value not in (None, ""):
                return str(value).strip()[:200]
        return None

    async def _fail(
        self, source: str, snapshot_id: str, record: Any, reason: str, ref: str | None = None
    ) -> RecordOutcome:
        payload = record if isinstance(record, dict) else {"value": str(record)[:200]}
        await self.failures.record(
            source=source,
            snapshot_id=snapshot_id,
            record_ref=ref,
            reason=reason,
            payload=payload,
        )
        return RecordOutcome("failed", reason)

    async def process_record(
        self,
        *,
        source: str,
        snapshot_id: str,
        record: Any,
        register: Register,
        observed_at: datetime | None = None,
    ) -> RecordOutcome:
        """Run one record through mapping, resolution, and core delivery."""
        ref: str | None = None
        try:
            if not isinstance(record, dict):
                return await self._fail(source, snapshot_id, record, "record is not an object")
            ref = self._external_ref(source, record)
            normalized = normalize_record(self._mapping(source), record)
            ref = normalized.external_id
            name = _record_name(normalized.fields)
            if name is None:
                return await self._fail(source, snapshot_id, record, "record has no name", ref)
            city = normalized.fields.get("city")
            nit = normalized.fields.get("nit")
            registered = await register(
                name,
                city if isinstance(city, str) else None,
                nit if isinstance(nit, str) else None,
            )
            if registered.status in SKIP_STATUSES:
                return RecordOutcome("skipped", registered.status)
            resolution = self.resolve(
                Candidate(
                    id=registered.id,
                    name=name,
                    city=city if isinstance(city, str) else None,
                    nit=nit if isinstance(nit, str) else None,
                    attempts=registered.attempts,
                )
            )
            if resolution.decision == "quarantine":
                return await self._fail(
                    source, snapshot_id, record, "quarantined by resolution", ref
                )
            if resolution.decision not in DELIVER_DECISIONS:
                return RecordOutcome("pending", resolution.decision)
            await self._deliver(normalized, name, resolution, observed_at or datetime.now(UTC))
            return RecordOutcome("delivered")
        except MappingError as exc:
            return await self._fail(source, snapshot_id, record, f"mapping: {exc}", ref)
        except httpx.HTTPStatusError as exc:
            reason = f"core rejected request: HTTP {exc.response.status_code}"
            return await self._fail(source, snapshot_id, record, reason, ref)
        except httpx.HTTPError as exc:
            reason = f"core unreachable: {type(exc).__name__}"
            return await self._fail(source, snapshot_id, record, reason, ref)
        except (KeyError, ValueError, TypeError) as exc:
            reason = f"unexpected core answer or record: {type(exc).__name__}"
            return await self._fail(source, snapshot_id, record, reason, ref)

    async def _deliver(
        self,
        normalized: NormalizedRecord,
        name: str,
        resolution: Resolution,
        observed_at: datetime,
    ) -> None:
        fields = normalized.fields
        city = fields.get("city")
        website = fields.get("website")
        company_body: dict[str, Any] = {"legal_name": name}
        if resolution.rues_nit:
            company_body["nit"] = resolution.rues_nit
        if isinstance(city, str):
            company_body["city"] = city
        if normalized.entity == "company":
            if isinstance(website, str):
                company_body["website"] = website
            company_body["source_ids"] = {normalized.source: normalized.external_id}
        company = await self._call(self.core.upsert_company, company_body)
        signal_body = {
            "company_id": str(UUID(str(company["company_id"]))),
            "source": normalized.source,
            "signal_type": normalized.signal_type,
            "observed_at": observed_at.isoformat(),
            "payload": _json_safe(fields),
            "source_external_id": normalized.external_id,
        }
        await self._call(self.core.create_signal, signal_body)

    async def _call(
        self, method: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]], body: dict[str, Any]
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            return await method(body)

        return await retry_with_backoff(
            operation,
            attempts=self.retry_attempts,
            base_delay_seconds=self.retry_base_delay_seconds,
            max_delay_seconds=self.retry_max_delay_seconds,
            retry_on=(httpx.TransportError,),
        )
