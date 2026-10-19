"""Wire the in-memory world for the offline end-to-end pipeline test (REQ-QA-004).

It holds fake repositories and fake Redis Streams, never a database or a network. The real
application code runs on top of them: the ingest webhook app and ``IngestRepository`` (over
``FakeIngestEngine``, a dict backed SQL engine), ``SnapshotFlow`` with ``EntityResolver``, the
Core app, ``upsert_company``, ``handle_score_event`` and ``relay_due_events`` (over
``FakeCoreRepository``), the ML app and ``ScoringService``, the Pulse ``process_event`` and app,
the Brief app and ``BriefService``, and the gateway app, which reaches every service through
``RoutingTransport`` instead of sockets. It relates to ``tests/unit/test_pipeline_flow.py`` (the
test), ``tests/unit/support_relay.py`` (``FakePulseDb``) and ``tests/conftest.py``.

Two ports stay faked beyond storage: the Brief ``CoreSource`` (core has no brief-facts route yet)
and the ``tenant_id`` that ingest does not send when it upserts a company. Both are switchable
and reported as production defects by the test.
"""

import json
import re
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace, TracebackType
from typing import Any
from uuid import UUID, uuid4

import httpx
from fakeredis import FakeAsyncRedis
from support_relay import FakePulseDb

from libs.common.events_dispatch import dispatch_event
from libs.common.rate_limiter import RateLimitDecision
from libs.common.redis_streams import ensure_consumer_group, read_consumer_batch
from libs.contracts.api import SignalCreate
from libs.domain.entity_resolution import EntityResolver, MockRuesLookup
from services.brief.app.core_source import CoreNotFound
from services.brief.app.models import BriefRules, CompanyFacts
from services.core.app.dead_letter import relay_due_events, stream_fields
from services.core.app.worker import build_handlers
from services.ingest.app.repository import IngestRepository
from services.ingest.app.to_core import (
    HttpCoreClient,
    SnapshotFlow,
    SqlFailureSink,
    SqlResolutionStore,
)
from services.pulse.app.consumer import process_event
from services.pulse.app.repository import PulseRepository

TENANT_ID = UUID("00000000-0000-4000-8000-0000000000a1")
DATASET_ID = "ds-maps-test"
EVENTS_STREAM = "invent.events"
INGEST_STREAM = "invent.ingest"


# --- redis -------------------------------------------------------------------------------


class ScriptlessRedis(FakeAsyncRedis):
    """FakeAsyncRedis that answers the owner-checked lock release without a Lua runtime."""

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:  # type: ignore[override]
        key, owner = keys_and_args[:numkeys], keys_and_args[numkeys]
        current = await self.get(key[0])
        if current is not None and current == owner:
            return await self.delete(key[0])
        return 0


async def drain(
    redis: Any,
    *,
    stream: str,
    group: str,
    consumer: str,
    handle: Callable[[dict[str, str]], Awaitable[None]],
) -> list[dict[str, str]]:
    """Read every entry of ``stream`` for ``group``, call ``handle`` and ack; return them."""
    await ensure_consumer_group(redis, stream, group)
    seen: list[dict[str, str]] = []
    while True:
        batch = await read_consumer_batch(
            redis, stream=stream, group=group, consumer=consumer, count=50, block=1
        )
        entries = [entry for _name, items in (batch or []) for entry in items]
        if not entries:
            return seen
        for message_id, fields in entries:
            await handle(dict(fields))
            await redis.xack(stream, group, message_id)
            seen.append(dict(fields))


# --- core --------------------------------------------------------------------------------


class CoreStore:
    """Rows of the ``core`` schema and the ``ml.lead_score`` read model."""

    def __init__(self) -> None:
        self.companies: dict[UUID, dict[str, Any]] = {}
        self.company_audit: list[dict[str, Any]] = []
        self.leads: dict[UUID, dict[str, Any]] = {}
        self.lead_audit: list[dict[str, Any]] = []
        self.processed: set[UUID] = set()
        self.signals: dict[UUID, dict[str, Any]] = {}
        self.outbox: list[dict[str, Any]] = []
        self.dead_letters: list[dict[str, Any]] = []
        self.scores: dict[UUID, dict[str, Any]] = {}
        self.score_history: list[dict[str, Any]] = []

    def snapshot(self) -> Any:
        return deepcopy(
            (
                self.companies,
                self.company_audit,
                self.leads,
                self.lead_audit,
                self.processed,
                self.signals,
                self.outbox,
                self.dead_letters,
            )
        )

    def restore(self, saved: Any) -> None:
        (
            self.companies,
            self.company_audit,
            self.leads,
            self.lead_audit,
            self.processed,
            self.signals,
            self.outbox,
            self.dead_letters,
        ) = saved

    def add_outbox(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        version: int,
        payload: dict[str, Any],
    ) -> None:
        self.outbox.append(
            {
                "id": len(self.outbox) + 1,
                "event_id": event_id,
                "tenant_id": tenant_id,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "event_type": event_type,
                "version": version,
                "payload": json.loads(json.dumps(payload, default=str)),
                "attempts": 0,
                "replay": None,
                "published_at": None,
                "dead_lettered_at": None,
                "next_attempt_at": None,
                "last_error": None,
            }
        )


class FakeUnitOfWork:
    """Commit on a clean exit and restore the store when the block raises."""

    def __init__(self, store: CoreStore, transaction: Any) -> None:
        self.store = store
        self.transaction = transaction
        self._saved: Any = None

    async def __aenter__(self) -> Any:
        self._saved = self.store.snapshot()
        return self.transaction

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.store.restore(self._saved)


class _Tx:
    def __init__(self, store: CoreStore) -> None:
        self.store = store

    async def lock_key(self, key: str) -> None:
        return None


class FakeCompanyTx(_Tx):
    async def find_company(self, nit: str | None, slug: str) -> dict[str, Any] | None:
        rows = list(self.store.companies.values())
        by_nit = [c for c in rows if nit is not None and c["nit"] == nit]
        match = by_nit[0] if by_nit else next((c for c in rows if c["slug"] == slug), None)
        return deepcopy(match) if match else None

    async def insert_company(self, row: dict[str, Any]) -> None:
        self.store.companies[row["id"]] = deepcopy(row)

    async def close_version(self, old: dict[str, Any], closed_at: datetime) -> None:
        self.store.company_audit.append(
            {"action": "company.version_closed", "id": old["id"], "valid_to": closed_at}
        )

    async def update_company(self, row: dict[str, Any]) -> None:
        self.store.companies[row["id"]] = deepcopy(row)

    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_id: UUID,
        version: int,
        payload: dict[str, Any],
    ) -> None:
        self.store.add_outbox(
            event_id=event_id,
            tenant_id=tenant_id,
            aggregate_type="company",
            aggregate_id=aggregate_id,
            event_type="company.updated",
            version=version,
            payload=payload,
        )


class FakeQualificationTx(_Tx):
    async def mark_processed(self, event_id: UUID) -> bool:
        if event_id in self.store.processed:
            return False
        self.store.processed.add(event_id)
        return True

    async def get_company_tenant(self, company_id: UUID) -> tuple[bool, UUID | None]:
        company = self.store.companies.get(company_id)
        return (company is not None, company["tenant_id"] if company else None)

    async def get_config(self, tenant_id: UUID) -> dict[str, Any] | None:
        return None

    async def find_leads(self, company_id: UUID) -> list[dict[str, Any]]:
        rows = [r for r in self.store.leads.values() if r["company_id"] == company_id]
        return [deepcopy(r) for r in sorted(rows, key=lambda r: (r["created_at"], str(r["id"])))]

    async def has_manual_transition(self, lead_id: UUID) -> bool:
        return any(
            a["action"] == "lead.status_changed" and a["id"] == lead_id
            for a in self.store.lead_audit
        )

    async def insert_lead(self, row: dict[str, Any]) -> None:
        self.store.leads[row["id"]] = deepcopy(row)

    async def update_stage(self, row: dict[str, Any]) -> None:
        self.store.leads[row["id"]].update(
            stage=row["stage"],
            version=row["version"],
            updated_at=row["updated_at"],
            last_transition_at=row["last_transition_at"],
        )

    async def add_audit(self, *, action: str, lead_id: UUID, payload: dict[str, Any]) -> None:
        self.store.lead_audit.append({"action": action, "id": lead_id, "payload": payload})

    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID | None,
        aggregate_id: UUID,
        version: int,
        payload: dict[str, Any],
    ) -> None:
        self.store.add_outbox(
            event_id=event_id,
            tenant_id=tenant_id,
            aggregate_type="lead",
            aggregate_id=aggregate_id,
            event_type="lead.created",
            version=version,
            payload=payload,
        )


class FakeOutboxTx(_Tx):
    async def claim_due(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        due = [
            deepcopy(r)
            for r in self.store.outbox
            if not r["published_at"]
            and not r["dead_lettered_at"]
            and (r["next_attempt_at"] is None or r["next_attempt_at"] <= now)
        ]
        return due[:limit]

    def _row(self, row_id: int) -> dict[str, Any]:
        return next(r for r in self.store.outbox if r["id"] == row_id)

    async def mark_published(self, row_id: int, now: datetime) -> None:
        self._row(row_id)["published_at"] = now

    async def record_failure(
        self, row_id: int, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None:
        self._row(row_id).update(
            attempts=attempts, next_attempt_at=next_attempt_at, last_error=error
        )

    async def dead_letter(
        self, row: dict[str, Any], *, attempts: int, error: str, now: datetime
    ) -> None:
        self.store.dead_letters.append(
            {"event_id": row["event_id"], "event_type": row["event_type"], "error": error}
        )
        self._row(row["id"]).update(attempts=attempts, last_error=error, dead_lettered_at=now)


class FakeCoreRepository:
    """In-memory stand-in for ``CoreRepository``: units of work plus the read queries."""

    def __init__(self, store: CoreStore) -> None:
        self.store = store

    async def close(self) -> None:
        return None

    def company_unit_of_work(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.store, FakeCompanyTx(self.store))

    def qualification_unit_of_work(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.store, FakeQualificationTx(self.store))

    def outbox_unit_of_work(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.store, FakeOutboxTx(self.store))

    async def create_signal(
        self, signal: SignalCreate, *, request_id: str | None
    ) -> tuple[UUID, UUID | None]:
        external_id = signal.source_external_id or str(uuid4())
        for existing in self.store.signals.values():
            if existing["source"] == signal.source and existing["external_id"] == external_id:
                return existing["id"], None
        signal_id, event_id = uuid4(), uuid4()
        payload = signal.model_dump(mode="json") | {
            "signal_id": str(signal_id),
            "source_external_id": external_id,
        }
        self.store.signals[signal_id] = {
            "id": signal_id,
            "company_id": signal.company_id,
            "source": signal.source,
            "external_id": external_id,
            "signal_type": signal.signal_type,
            "version": 1,
        }
        company = self.store.companies.get(signal.company_id)
        self.store.add_outbox(
            event_id=event_id,
            tenant_id=company["tenant_id"] if company else None,
            aggregate_type="signal",
            aggregate_id=signal_id,
            event_type="signal.created",
            version=1,
            payload=payload | {"request_id": request_id},
        )
        return signal_id, event_id

    def _lead_row(self, lead: dict[str, Any], *, detail: bool) -> dict[str, Any]:
        company = self.store.companies[lead["company_id"]]
        score = self.store.scores.get(lead["company_id"])
        row = {
            "id": lead["id"],
            "company_id": lead["company_id"],
            "legal_name": company["legal_name"],
            "service_line": lead["service_line"],
            "stage": lead["stage"],
            "city": lead["city"],
            "segment": company["sector_code"],
            "score": score["score"] if score else None,
            "version": lead["version"],
            "created_at": lead["created_at"],
            "updated_at": lead["updated_at"],
        }
        if detail:
            row |= {
                "source": lead["source"],
                "qualification_reason": lead["qualification_reason"],
                "last_transition_at": lead.get("last_transition_at"),
            }
        return row

    async def list_companies(
        self, limit: int, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        rows = sorted(self.store.companies.values(), key=lambda c: c["updated_at"], reverse=True)
        keys = (
            "id slug legal_name trade_name sector_code employee_count website city version "
            "updated_at"
        ).split()
        return [{key: row[key] for key in keys} for row in rows[:limit]], None

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
    ) -> tuple[list[dict[str, Any]], str | None]:
        rows = [self._lead_row(lead, detail=False) for lead in self.store.leads.values()]
        wanted = {"stage": stage, "service_line": service_line, "city": city, "segment": segment}
        rows = [r for r in rows if all(v is None or r[k] == v for k, v in wanted.items())]
        if min_score is not None:
            rows = [r for r in rows if r["score"] is not None and r["score"] >= min_score]
        if tenant_id is not None:
            rows = [r for r in rows if self.store.leads[r["id"]]["tenant_id"] == tenant_id]
        rows.sort(key=lambda r: (r["updated_at"], str(r["id"])), reverse=True)
        return rows[:limit], None

    async def get_lead(self, lead_id: UUID, tenant_id: UUID | None = None) -> dict[str, Any] | None:
        lead = self.store.leads.get(lead_id)
        if lead is None or (tenant_id is not None and lead["tenant_id"] != tenant_id):
            return None
        return self._lead_row(lead, detail=True)


# --- ml ----------------------------------------------------------------------------------


class FakeMlRepository:
    """In-memory ``MlRepository`` writing the shared ``ml.lead_score`` read model."""

    def __init__(self, store: CoreStore) -> None:
        self.store = store

    async def close(self) -> None:
        return None

    async def upsert_score(
        self,
        *,
        company_id: UUID,
        score: int,
        is_hot: bool,
        source_version: str,
        score_version: str,
    ) -> None:
        self.store.scores[company_id] = {
            "company_id": company_id,
            "score": score,
            "is_hot": is_hot,
            "stale": False,
            "source_version": source_version,
            "score_version": score_version,
        }
        self.store.score_history.append({"company_id": company_id, "score": score})

    async def latest_score(self, company_id: UUID) -> dict[str, Any] | None:
        return self.store.scores.get(company_id)

    async def mark_stale(self, company_id: UUID) -> None:
        self.store.scores[company_id]["stale"] = True

    async def load_weights(self, tenant_id: UUID) -> Any:
        from libs.domain.scoring import ScoreWeights

        return ScoreWeights()


# --- pulse -------------------------------------------------------------------------------


class FakePulseRepository(PulseRepository):
    """``PulseRepository`` whose market overview sums the ``FakePulseDb`` rollup."""

    def __init__(self, db: FakePulseDb) -> None:
        super().__init__(None)
        self.db = db

    async def market_overview(self, *, tenant_id: UUID | None) -> dict[str, object]:
        rows = [
            row for key, row in self.db.rollup.items() if tenant_id is None or key[0] == tenant_id
        ]
        return {
            "signals": sum(r["signal_count"] for r in rows),
            "leads": sum(r["lead_count"] for r in rows),
            "hot_leads": sum(r["hot_lead_count"] for r in rows),
            "as_of": datetime.now(UTC) if rows else None,
        }


# --- brief -------------------------------------------------------------------------------


class FakeCoreSource:
    """Brief ``CoreSource`` that derives facts from the core store (core has no such route)."""

    def __init__(self, store: CoreStore) -> None:
        self.store = store

    async def get_facts(self, company_id: UUID, tenant_id: UUID | None) -> CompanyFacts:
        company = self.store.companies.get(company_id)
        if company is None or (tenant_id is not None and company["tenant_id"] != tenant_id):
            raise CoreNotFound(str(company_id))
        signals = [s for s in self.store.signals.values() if s["company_id"] == company_id]
        score = self.store.scores.get(company_id)
        return CompanyFacts(
            company_id=company_id,
            version=company["version"],
            signal_versions_sum=sum(s["version"] for s in signals),
            score=score["score"] if score else None,
            sector=company["sector_code"],
            city=company["city"],
            signal_codes=sorted({s["signal_type"] for s in signals}),
            rues_status="pending",
            days_since_contact=None,
        )

    async def get_rules(self, tenant_id: UUID | None) -> tuple[BriefRules, int]:
        return BriefRules(), 0


# --- ingest ------------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = len(self._rows)

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, Any]:
        assert len(self._rows) == 1
        return self._rows[0]

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalar_one(self) -> Any:
        assert self._scalar is not None
        return self._scalar


class _Connection:
    def __init__(self, engine: "FakeIngestEngine") -> None:
        self.engine = engine

    async def __aenter__(self) -> "_Connection":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        return self.engine.dispatch(" ".join(str(statement).split()), params or {})


class FakeIngestEngine:
    """Hold the ``ingest`` tables and answer the SQL of ``IngestRepository`` and its helpers."""

    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []
        self.candidates: dict[tuple[UUID, str], dict[str, Any]] = {}
        self.match_log: list[dict[str, Any]] = []
        self.rues_queue: dict[str, dict[str, Any]] = {}
        self.quarantine: list[dict[str, Any]] = []
        self.failed_records: list[dict[str, Any]] = []

    def begin(self) -> _Connection:
        return _Connection(self)

    def connect(self) -> _Connection:
        return _Connection(self)

    async def dispose(self) -> None:
        return None

    def _snapshot(self, row_id: UUID) -> dict[str, Any]:
        return next(s for s in self.snapshots if s["id"] == row_id)

    def dispatch(self, sql: str, params: dict[str, Any]) -> _Rows:
        if sql.startswith("INSERT INTO ingest.raw_snapshot"):
            for row in self.snapshots:
                if (
                    row["snapshot_id"] == params["snapshot_id"]
                    and row["payload_sha256"] == params["payload_sha256"]
                ):
                    return _Rows()
            row = {
                "id": uuid4(),
                "seq": len(self.snapshots),
                "snapshot_id": params["snapshot_id"],
                "dataset_id": params["dataset_id"],
                "payload": json.loads(params["payload"]),
                "payload_sha256": params["payload_sha256"],
            }
            self.snapshots.append(row)
            return _Rows(scalar=row["id"])
        if sql.startswith("SELECT id FROM ingest.raw_snapshot"):
            row = next(
                s
                for s in self.snapshots
                if s["snapshot_id"] == params["snapshot_id"]
                and s["payload_sha256"] == params["payload_sha256"]
            )
            return _Rows(scalar=row["id"])
        if sql.startswith("SELECT payload, dataset_id, snapshot_id FROM ingest.raw_snapshot"):
            return _Rows([self._snapshot(params["id"])] if self._exists(params["id"]) else [])
        if sql.startswith("SELECT prev.payload FROM ingest.raw_snapshot AS cur"):
            current = self._snapshot(params["id"])
            earlier = [
                s
                for s in self.snapshots
                if s["snapshot_id"] == current["snapshot_id"] and s["seq"] < current["seq"]
            ]
            return _Rows([max(earlier, key=lambda s: s["seq"])] if earlier else [])
        if sql.startswith("INSERT INTO ingest.entity_candidate"):
            key = (params["raw_snapshot_id"], params["candidate_key"])
            existing = self.candidates.get(key)
            if existing is not None:
                existing["name"] = params["name"]
            else:
                existing = {
                    "id": uuid4(),
                    "resolution_attempts": 0,
                    "status": "pending",
                    "name": params["name"],
                    "city": params["city"],
                    "candidate_nit": params["candidate_nit"],
                    "normalized_name": params["normalized_name"],
                }
                self.candidates[key] = existing
            return _Rows([existing])
        return self._resolution_dispatch(sql, params)

    def _exists(self, row_id: UUID) -> bool:
        return any(s["id"] == row_id for s in self.snapshots)

    def _candidate(self, candidate_id: str) -> dict[str, Any]:
        return next(c for c in self.candidates.values() if str(c["id"]) == candidate_id)

    def _resolution_dispatch(self, sql: str, params: dict[str, Any]) -> _Rows:
        if sql.startswith("INSERT INTO ingest.entity_match_log"):
            self.match_log.append(dict(params))
        elif sql.startswith("UPDATE ingest.entity_candidate SET status"):
            self._candidate(params["candidate_id"]).update(
                status=params["status"], resolution_attempts=params["attempts"]
            )
        elif sql.startswith("INSERT INTO ingest.rues_lookup_queue"):
            self.rues_queue[params["candidate_id"]] = {"status": "queued", **params}
        elif sql.startswith("UPDATE ingest.rues_lookup_queue"):
            self.rues_queue[params["candidate_id"]]["status"] = params["status"]
        elif sql.startswith("INSERT INTO ingest.quarantine"):
            self.quarantine.append(dict(params))
        elif sql.startswith("INSERT INTO ingest.failed_record"):
            self.failed_records.append(
                {**params, "payload": json.loads(params["payload"])},
            )
        else:
            raise AssertionError(f"unexpected SQL: {sql[:120]}")
        return _Rows()


class TenantStampingCore:
    """Wrap ``HttpCoreClient`` and add the tenant that ingest does not send (defect D-1)."""

    def __init__(self, inner: HttpCoreClient, tenant_id: UUID) -> None:
        self.inner = inner
        self.tenant_id = tenant_id

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.inner.upsert_company(body | {"tenant_id": str(self.tenant_id)})

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.inner.create_signal(body)


class WiredIngestRepository(IngestRepository):
    """``IngestRepository`` on the fake engine and fake Redis, built like ``processor.main``."""

    def __init__(self, engine: FakeIngestEngine, redis: Any) -> None:
        self.engine = engine  # type: ignore[assignment]
        self.redis = redis
        self.enqueue_mark_ttl_seconds = 3600


# --- transport and world -----------------------------------------------------------------


class RoutingTransport(httpx.AsyncBaseTransport):
    """Send each request to the ASGI app that owns the URL port, like the compose network."""

    def __init__(self, apps: dict[int, Any]) -> None:
        self._transports = {port: httpx.ASGITransport(app=app) for port, app in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._transports.get(request.url.port or 0)
        if transport is None:
            raise httpx.ConnectError("no such service", request=request)
        return await transport.handle_async_request(request)


class PermissiveLimiter:
    """Rate limiter stub that always allows."""

    async def check(self, identity: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, limit=1000, remaining=999)


_PORT = re.compile(r":(\d+)")


def port_of(url: str) -> int:
    match = _PORT.search(url)
    assert match is not None
    return int(match.group(1))


class World:
    """All fakes plus the real apps, wired together; ``stamp_tenant`` toggles the ingest shim."""

    def __init__(self, redis: Any, *, stamp_tenant: bool = True) -> None:
        from services.brief.app import main as brief_main
        from services.brief.app.repository import BriefRepository
        from services.core.app import main as core_main
        from services.ingest.app import main as ingest_main
        from services.ml.app import main as ml_main
        from services.pulse.app import main as pulse_main

        self.redis = redis
        self.tenant_id = TENANT_ID
        self.core_store = CoreStore()
        self.core_repo = FakeCoreRepository(self.core_store)
        self.pulse_db = FakePulseDb()
        self.ingest_engine = FakeIngestEngine()
        self.ingest_repo = WiredIngestRepository(self.ingest_engine, redis)
        self.core_source = FakeCoreSource(self.core_store)

        self.ingest_app, self.core_app = ingest_main.app, core_main.app
        self.ml_app, self.pulse_app, self.brief_app = ml_main.app, pulse_main.app, brief_main.app
        self.ingest_app.state.repository = self.ingest_repo
        self.core_app.state.repository = self.core_repo
        self.core_app.state.hot_alerts = None
        self.ml_app.state.repository = FakeMlRepository(self.core_store)
        self.ml_app.state.redis = redis
        self.pulse_app.state.repository = FakePulseRepository(self.pulse_db)
        self.brief_app.state.brief = brief_main.BriefState(
            BriefRepository(None), redis, self.core_source
        )

        settings = ingest_main.settings
        self.ports = {
            "ingest": port_of(settings.ingest_url),
            "core": port_of(settings.core_url),
            "ml": port_of(settings.ml_url),
            "pulse": port_of(settings.pulse_url),
            "brief": port_of(settings.brief_url),
        }
        self.transport = RoutingTransport(
            {
                self.ports["ingest"]: self.ingest_app,
                self.ports["core"]: self.core_app,
                self.ports["ml"]: self.ml_app,
                self.ports["pulse"]: self.pulse_app,
                self.ports["brief"]: self.brief_app,
            }
        )
        http_client = httpx.AsyncClient(transport=self.transport, base_url=settings.core_url)
        self.core_http = HttpCoreClient(
            settings.core_url, settings.internal_service_token, client=http_client
        )
        core_client: Any = (
            TenantStampingCore(self.core_http, TENANT_ID) if stamp_tenant else self.core_http
        )
        self.resolution_store = SqlResolutionStore()
        self.ingest_repo.resolution_store = self.resolution_store
        rues = MockRuesLookup([])
        self.ingest_repo.flow = SnapshotFlow(
            core=core_client,
            failures=SqlFailureSink(self.ingest_engine),  # type: ignore[arg-type]
            resolve=lambda candidate: EntityResolver(
                rues, self.resolution_store, now=datetime.now(UTC)
            ).resolve(candidate),
            dataset_sources={DATASET_ID: "maps"},
            retry_attempts=1,
            retry_base_delay_seconds=0,
            retry_max_delay_seconds=0,
        )

    def gateway_shim(self, real_client: Any) -> SimpleNamespace:
        """Return the ``httpx`` stand-in that makes the gateway call the ASGI apps."""

        def factory(*args: Any, **kwargs: Any) -> Any:
            return real_client(*args, transport=self.transport, **kwargs)

        return SimpleNamespace(
            AsyncClient=factory,
            HTTPError=httpx.HTTPError,
            TransportError=httpx.TransportError,
            Response=httpx.Response,
        )

    async def close(self) -> None:
        await self.core_http.aclose()

    # Stage runners: each one is the loop body of the matching worker, over the fake stream.

    async def run_processor(self) -> list[int]:
        counts: list[int] = []

        async def handle(fields: dict[str, str]) -> None:
            counts.append(await self.ingest_repo.process_snapshot(UUID(fields["raw_snapshot_id"])))

        await drain(
            self.redis,
            stream=INGEST_STREAM,
            group="ingest-processors",
            consumer="ingest-worker",
            handle=handle,
        )
        return counts

    async def run_relay(self) -> dict[str, int]:
        async def publish(row: dict[str, Any]) -> None:
            await self.redis.xadd(EVENTS_STREAM, stream_fields(row))

        return await relay_due_events(self.core_repo.outbox_unit_of_work(), publish)

    async def run_pulse_consumer(self) -> list[dict[str, str]]:
        async def handle(fields: dict[str, str]) -> None:
            async with self.pulse_db.begin() as connection:
                await process_event(connection, fields)  # type: ignore[arg-type]

        return await drain(
            self.redis,
            stream=EVENTS_STREAM,
            group="pulse",
            consumer="pulse-worker",
            handle=handle,
        )

    async def run_core_worker(self) -> list[dict[str, str]]:
        handlers = build_handlers(self.core_repo.qualification_unit_of_work)

        async def noop_ack() -> None:
            return None

        async def handle(fields: dict[str, str]) -> None:
            await dispatch_event(fields, handlers, noop_ack)

        return await drain(
            self.redis, stream=EVENTS_STREAM, group="core", consumer="core-worker", handle=handle
        )
