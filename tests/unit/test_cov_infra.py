"""Cover shared infrastructure modules and the core repository through fakes (REQ-QA-003).

The tests exercise ``libs/common`` db, Redis and rate limiter helpers, the core
worker loop, the SQL paths of ``services/core/app/repository.py`` and the Brief
``HttpCoreSource``. They relate to ``tests/unit/support_cov.py`` and run with no
Postgres, Redis or network.
"""

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import fakeredis.aioredis
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from libs.common import db, rate_limiter, redis_client
from libs.common.internal_auth import verify_service_token
from libs.common.pagination import encode_cursor
from libs.contracts.api import SignalCreate
from services.brief.app import core_source
from services.brief.app.core_source import CoreNotFound, CoreUnavailable, HttpCoreSource
from services.core.app import repository as repo
from services.core.app import worker
from tests.unit.support_cov import FakeConnection, FakeEngine, FakeEvalRedis, FakeResult
from tests.unit.support_outbox import StopLoop

NOW = datetime(2026, 1, 1, tzinfo=UTC)
TENANT = uuid4()
ID = uuid4()


# --- libs/common/db.py -------------------------------------------------------------------


def test_engine_settings_asyncpg_disables_prepared_statement_caches() -> None:
    url, options = db.engine_settings(
        "postgresql+asyncpg://u:p@h/d",
        pool_size=3,
        max_overflow=1,
        pool_timeout=2.0,
        connect_timeout=4.0,
    )
    assert "prepared_statement_cache_size=0" in url
    assert ":p@" in url
    assert options["pool_size"] == 3
    assert options["connect_args"] == {"timeout": 4.0, "statement_cache_size": 0}


def test_engine_settings_psycopg_uses_fixed_pool_and_no_prepare() -> None:
    url, options = db.engine_settings(
        "postgresql+psycopg://u:p@h/d",
        pool_size=9,
        max_overflow=0,
        pool_timeout=1.0,
        connect_timeout=0.2,
    )
    assert url == "postgresql+psycopg://u:p@h/d"
    assert options["pool_size"] == db.PSYCOPG_POOL_SIZE
    assert options["connect_args"] == {"connect_timeout": 1, "prepare_threshold": None}


def test_engine_settings_other_dialect_keeps_shared_limits() -> None:
    url, options = db.engine_settings(
        "sqlite+aiosqlite://",
        pool_size=5,
        max_overflow=2,
        pool_timeout=1.5,
        connect_timeout=3.0,
    )
    assert url == "sqlite+aiosqlite://"
    assert options["pool_size"] == 5
    assert options["max_overflow"] == 2
    assert options["connect_args"] == {"timeout": 3.0}


def test_create_engine_without_url_returns_none() -> None:
    assert db.create_engine(None) is None
    assert db.create_engine("") is None
    assert db.session_factory(None) is None


def test_create_engine_and_session_factory_do_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_create(url: str, **options: Any) -> str:
        captured["url"] = url
        captured["options"] = options
        return "engine"

    monkeypatch.setattr(db, "create_async_engine", fake_create)
    monkeypatch.setattr(
        db,
        "get_settings",
        lambda: SimpleNamespace(
            database_pool_size=7,
            database_max_overflow=1,
            database_pool_timeout_seconds=2.0,
            database_connect_timeout_seconds=3.0,
        ),
    )
    assert db.create_engine("postgresql+asyncpg://u:p@h/d") == "engine"
    assert captured["options"]["pool_size"] == 7
    db.create_engine("postgresql+asyncpg://u:p@h/d", pool_size=1)
    assert captured["options"]["pool_size"] == 1


async def test_session_factory_wraps_engine() -> None:
    engine = db.create_engine("postgresql+asyncpg://u:p@127.0.0.1:1/d")
    assert engine is not None
    factory = db.session_factory(engine)
    assert factory is not None
    await engine.dispose()


async def test_check_database_false_without_url() -> None:
    assert await db.check_database(None) is False


async def test_check_database_true_and_false(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(db, "create_engine", lambda url, pool_size=None: engine)
    assert await db.check_database("x") is True
    assert engine.disposed is True
    assert "SELECT 1" in engine.connection.sql

    class Broken(FakeEngine):
        def connect(self) -> Any:
            raise OSError("down")

    broken = Broken()
    monkeypatch.setattr(db, "create_engine", lambda url, pool_size=None: broken)
    assert await db.check_database("x") is False
    assert broken.disposed is True


# --- libs/common/redis_client.py ---------------------------------------------------------


async def test_create_redis_decodes_responses() -> None:
    client = redis_client.create_redis("redis://127.0.0.1:1/0")
    assert client.connection_pool.connection_kwargs["decode_responses"] is True
    await client.aclose()


async def test_check_redis_true_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client, "create_redis", lambda url: fake)
    assert await redis_client.check_redis("redis://x") is True

    class Broken:
        closed = False

        async def ping(self) -> bool:
            raise ConnectionError("down")

        async def aclose(self) -> None:
            Broken.closed = True

    monkeypatch.setattr(redis_client, "create_redis", lambda url: Broken())
    assert await redis_client.check_redis("redis://x") is False
    assert Broken.closed is True


# --- libs/common/rate_limiter.py ---------------------------------------------------------


@pytest.mark.parametrize(("limit", "window"), [(0, 1), (1, 0)])
def test_rate_limiter_rejects_non_positive_values(limit: int, window: int) -> None:
    with pytest.raises(ValueError):
        rate_limiter.RedisSlidingWindowRateLimiter(
            cast(Any, FakeEvalRedis([])), limit=limit, window_seconds=window
        )


async def test_rate_limiter_allowed_decision_and_hashed_key() -> None:
    fake = FakeEvalRedis([1, 2, 0, 0])
    limiter = rate_limiter.RedisSlidingWindowRateLimiter(
        cast(Any, fake), limit=5, window_seconds=60
    )
    decision = await limiter.check("user@example.com")
    assert decision == rate_limiter.RateLimitDecision(True, 5, 3, None)
    args = fake.calls[0]
    assert args[1] == 1
    assert args[2].startswith("invent:rate:")
    assert "user@example.com" not in args[2]
    assert args[3] == "60"
    assert args[5] == "5"


async def test_rate_limiter_blocked_decision_has_retry_after() -> None:
    limiter = rate_limiter.RedisSlidingWindowRateLimiter(
        cast(Any, FakeEvalRedis([0, 5, "1000", 0])), limit=5, window_seconds=60
    )
    decision = await limiter.check("a")
    assert decision.allowed is False
    assert decision.remaining == 0
    assert decision.retry_after_seconds == 1
    limiter = rate_limiter.RedisSlidingWindowRateLimiter(
        cast(Any, FakeEvalRedis([0, 7, "1000", 42])), limit=5, window_seconds=60
    )
    assert (await limiter.check("a")).retry_after_seconds == 42


# --- services/core/app/worker.py ---------------------------------------------------------


async def test_build_handlers_routes_score_event(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, Mapping[str, str], Any]] = []

    async def fake_handle(uow: Any, fields: Mapping[str, str], on_hot: Any = None) -> None:
        seen.append((uow, fields, on_hot))

    monkeypatch.setattr(worker, "handle_score_event", fake_handle)
    hot = cast(Any, object())
    handlers = worker.build_handlers(lambda: cast(Any, "uow"), hot)
    assert set(handlers) == {"lead.score_updated"}
    await handlers["lead.score_updated"]({"event_type": "lead.score_updated"})
    assert seen == [("uow", {"event_type": "lead.score_updated"}, hot)]


async def test_import_loop_sleeps_when_idle_and_survives_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes: list[Any] = ["done", RuntimeError("boom"), None]
    sleeps: list[float] = []

    async def fake_process(factory: Any) -> str | None:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return cast("str | None", outcome)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise StopLoop

    monkeypatch.setattr(worker, "process_next_job", fake_process)
    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
    repository = cast(Any, SimpleNamespace(import_unit_of_work=lambda: None))
    with pytest.raises(StopLoop):
        await worker.import_loop(repository, 0.5)
    assert sleeps == [0.5, 0.5]


def _worker_settings() -> SimpleNamespace:
    return SimpleNamespace(
        database_url="postgresql://fake/db",
        redis_url="redis://fake/0",
        hot_lead_webhook_url=None,
        worker_failure_sleep_seconds=0,
        worker_stream_block_ms=1,
    )


async def test_main_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(database_url=None))
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await worker.main()


async def test_main_acks_handled_invalid_and_ignores_failed_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    closed: list[str] = []

    class Repo:
        engine = object()

        def qualification_unit_of_work(self) -> None:
            return None

        def import_unit_of_work(self) -> None:
            return None

        async def close(self) -> None:
            closed.append("repo")

    async def never_process(factory: Any) -> None:
        return None

    handled: list[str] = []

    async def on_score(fields: Mapping[str, str]) -> None:
        handled.append(fields["event_id"])
        if fields["event_id"] == "bad":
            raise ValueError("invalid")
        if fields["event_id"] == "fail":
            raise RuntimeError("transient")

    monkeypatch.setattr(worker, "get_settings", _worker_settings)
    monkeypatch.setattr(worker, "CoreRepository", lambda url: Repo())
    monkeypatch.setattr(worker, "create_redis", lambda url: redis)
    monkeypatch.setattr(worker, "process_next_job", never_process)
    monkeypatch.setattr(worker, "build_handlers", lambda uow, hot: {"lead.score_updated": on_score})
    for event_id in ("ok", "bad", "fail"):
        await redis.xadd(
            worker.STREAM_NAME, {"event_type": "lead.score_updated", "event_id": event_id}
        )
    await redis.xadd(worker.STREAM_NAME, {"event_type": "unknown.type", "event_id": "u"})

    real_read = worker.read_consumer_batch
    reads = 0

    async def limited_read(*args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        reads += 1
        if reads > 2:
            raise StopLoop
        return await real_read(*args, **kwargs)

    monkeypatch.setattr(worker, "read_consumer_batch", limited_read)
    with pytest.raises(StopLoop):
        await worker.main()
    assert handled[:3] == ["ok", "bad", "fail"]
    assert closed == ["repo"]
    # ok, bad and unknown are acked; the failed entry stays pending for redelivery.
    pending = await redis.xpending(worker.STREAM_NAME, worker.GROUP_NAME)
    assert pending["pending"] == 1
    await asyncio.sleep(0)


# --- services/brief/app/core_source.py ---------------------------------------------------


def _source(handler: Any) -> HttpCoreSource:
    source = HttpCoreSource("http://core", "tok", timeout_seconds=1.0)
    source._client = httpx.AsyncClient(
        base_url="http://core",
        transport=httpx.MockTransport(handler),
    )
    return source


async def test_core_source_get_facts_sends_token_and_tenant() -> None:
    company = uuid4()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"version": 3, "score": 80, "sector": "6201"})

    source = _source(handler)
    facts = await source.get_facts(company, TENANT)
    await source.close()
    assert facts.company_id == company
    assert facts.version == 3
    assert seen[0].url.path == f"/internal/core/companies/{company}/brief-facts"
    assert seen[0].headers["X-Tenant-Id"] == str(TENANT)
    assert verify_service_token(seen[0].headers["X-Service-Token"], "tok") == "brief"


async def test_core_source_get_facts_without_tenant_omits_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    source = _source(handler)
    await source.get_facts(uuid4(), None)
    await source.close()
    assert "X-Tenant-Id" not in seen[0].headers


async def test_core_source_maps_errors() -> None:
    def not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    def bad_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    def transport_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(CoreNotFound):
        await _source(not_found).get_facts(uuid4(), TENANT)
    with pytest.raises(CoreUnavailable, match="503"):
        await _source(server_error).get_facts(uuid4(), TENANT)
    with pytest.raises(CoreUnavailable, match="refused"):
        await _source(transport_error).get_facts(uuid4(), TENANT)
    with pytest.raises(httpx.HTTPStatusError):
        await _source(bad_request).get_facts(uuid4(), TENANT)


async def test_core_source_get_rules() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/core/admin/tenant-config"
        return httpx.Response(
            200, json={"version": 4, "brief_rules": {"disabled_alert_codes": ["x"]}}
        )

    source = _source(handler)
    rules, version = await source.get_rules(TENANT)
    assert rules.disabled_alert_codes == ["x"]
    assert version == 4
    defaults, zero = await source.get_rules(None)
    assert zero == 0
    assert defaults.disabled_alert_codes == []

    def no_rules(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"brief_rules": None})

    rules, version = await _source(no_rules).get_rules(TENANT)
    assert version == 0
    assert rules.weights == {}


def test_core_source_module_exports() -> None:
    assert issubclass(core_source.CoreNotFound, Exception)


# --- services/core/app/repository.py -----------------------------------------------------


def _uow(cls: Any, results: list[FakeResult] | None = None) -> tuple[Any, FakeEngine]:
    engine = FakeEngine(results)
    return cls(cast(AsyncEngine, engine)), engine


async def test_unit_of_work_commits_and_propagates_exit() -> None:
    unit, engine = _uow(repo.SqlCompanyUnitOfWork)
    async with unit as tx:
        await tx.lock_key("k")
    assert engine.log == ["enter:begin", "exit:begin:ok"]
    assert engine.connection.calls[0][1] == {"key": "company:k"}
    unit, engine = _uow(repo.SqlCompanyUnitOfWork)
    with pytest.raises(KeyError):
        async with unit:
            raise KeyError("x")
    assert engine.log[-1] == "exit:begin:error"


async def test_company_transaction_sql_paths() -> None:
    row: dict[str, object] = {
        "id": ID,
        "tenant_id": TENANT,
        "nit": "1",
        "slug": "acme",
        "legal_name": "Acme",
        "trade_name": None,
        "sector_code": "6201",
        "employee_count": 3,
        "website": None,
        "city": "Bogota",
        "source_flags": {"a": True},
        "version": 2,
        "valid_from": NOW,
        "updated_at": NOW,
    }
    unit, engine = _uow(repo.SqlCompanyUnitOfWork, [FakeResult([row]), FakeResult()])
    async with unit as tx:
        assert await tx.find_company("1", "acme") == row
        assert await tx.find_company(None, "none") is None
        await tx.insert_company(row)
        await tx.update_company(row)
        await tx.close_version(row, NOW + timedelta(days=1))
        await tx.add_outbox_event(
            event_id=ID, tenant_id=TENANT, aggregate_id=ID, version=2, payload={"at": NOW}
        )
    calls = engine.connection.calls
    assert "FOR UPDATE" in calls[0][0]
    assert calls[2][1]["source_flags"] == '{"a": true}'
    closed = json.loads(calls[4][1]["payload"])
    assert closed["valid_to"].startswith("2026-01-02")
    assert "id" not in closed
    assert "company.updated" in calls[5][0]


async def test_lead_transaction_sql_paths() -> None:
    lead: dict[str, object] = {"id": ID, "stage": "new", "version": 1}
    idem: dict[str, object] = {"request_hash": "h", "response_body": {"id": "x"}}
    unit, engine = _uow(repo.SqlLeadUnitOfWork)
    engine.connection.route("FROM core.idempotency_key", FakeResult([idem]))
    engine.connection.route("FROM core.lead", FakeResult([lead]))
    async with unit as tx:
        await tx.lock_key("k")
        assert await tx.get_idempotency(TENANT, "key") == idem
        assert await tx.get_idempotency(TENANT, "other") is None
        await tx.save_idempotency(
            tenant_id=TENANT,
            key="key",
            request_hash="h",
            response={"id": "x"},
            expires_at=NOW,
        )
        await tx.insert_lead(
            {
                "id": ID,
                "tenant_id": TENANT,
                "company_id": ID,
                "service_line": "s",
                "city": None,
                "source": "manual",
                "stage": "new",
                "qualification_reason": {"r": 1},
                "version": 1,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
        assert await tx.get_lead(ID, TENANT) == lead
        assert await tx.get_lead(ID, None) is None
        await tx.update_stage(
            {"id": ID, "stage": "hot", "version": 2, "updated_at": NOW, "last_transition_at": NOW}
        )
        await tx.add_audit(action="lead.status_changed", lead_id=ID, payload={"a": NOW})
        await tx.add_outbox_event(
            event_id=ID, tenant_id=None, aggregate_id=ID, version=1, payload={}
        )
    calls = engine.connection.calls
    assert json.loads(calls[3][1]["response"]) == {"id": "x"}
    assert json.loads(calls[4][1]["qualification_reason"]) == {"r": 1}
    assert "lead.created" in calls[-1][0]


async def test_action_transaction_sql_paths() -> None:
    lead: dict[str, object] = {"id": ID}
    unit, engine = _uow(repo.SqlActionUnitOfWork)
    engine.connection.route("FROM core.lead WHERE", FakeResult([lead]))
    engine.connection.route("FROM core.lead_activity", FakeResult([{"id": ID}]))
    engine.connection.route("FROM core.project", FakeResult(scalar=ID))
    async with unit as tx:
        assert await tx.get_lead(ID, TENANT) == lead
        await tx.insert_outcome({"id": ID})
        assert await tx.find_activity(TENANT, "k") == {"id": ID}
        assert await tx.find_activity(TENANT, "k2") is None
        await tx.insert_activity(
            {
                "id": ID,
                "tenant_id": TENANT,
                "lead_id": ID,
                "activity_type": "call",
                "channel": "phone",
                "source": "ui",
                "payload": {"n": 1},
                "occurred_at": NOW,
                "created_by": "u",
                "idempotency_key": "k",
            }
        )
        assert await tx.find_project_for_lead(ID) == ID
        assert await tx.find_project_for_lead(ID) is None
        await tx.insert_project({"id": ID, "metadata": {"m": 1}})
        await tx.add_outbox_event(
            event_id=ID,
            tenant_id=TENANT,
            aggregate_type="project",
            aggregate_id=ID,
            event_type="project.created",
            payload={},
        )
    sql = engine.connection.sql
    assert "core.lead_outcome" in sql
    assert "core.project" in sql
    assert engine.connection.calls[-1][1]["event_type"] == "project.created"


def _config_row() -> dict[str, object]:
    return {
        "tenant_id": TENANT,
        "version": 1,
        "scoring_rules": {"s": 1},
        "brief_rules": {"b": 1},
        "bright_data_budget_usd": 10,
        "metadata": {},
        "updated_by": "u",
        "updated_at": NOW,
    }


async def test_config_transaction_sql_paths() -> None:
    row = _config_row()
    unit, engine = _uow(repo.SqlConfigUnitOfWork, [FakeResult([row]), FakeResult()])
    async with unit as tx:
        assert await tx.get_config(TENANT) == row
        assert await tx.get_config(TENANT) is None
        await tx.insert_config(row)
        await tx.close_version(row, NOW)
        await tx.update_config(row)
        await tx.add_outbox_event(
            event_id=ID, tenant_id=TENANT, aggregate_id=TENANT, version=1, payload={}
        )
    calls = engine.connection.calls
    assert json.loads(calls[2][1]["scoring_rules"]) == {"s": 1}
    assert calls[3][1]["closed_at"] == NOW
    assert "tenant.config_updated" in calls[-1][0]


async def test_qualification_transaction_sql_paths() -> None:
    lead: dict[str, object] = {"id": ID, "stage": "new"}
    unit, engine = _uow(
        repo.SqlQualificationUnitOfWork,
        [
            FakeResult(scalar=ID),
            FakeResult(),
            FakeResult(tuples=[(TENANT,)]),
            FakeResult(),
            FakeResult([_config_row()]),
            FakeResult([lead, lead]),
            FakeResult(tuples=[(1,)]),
            FakeResult(),
        ],
    )
    async with unit as tx:
        assert await tx.mark_processed(ID) is True
        assert await tx.mark_processed(ID) is False
        assert await tx.get_company_tenant(ID) == (True, TENANT)
        assert await tx.get_company_tenant(ID) == (False, None)
        assert (await tx.get_config(TENANT)) is not None
        assert await tx.find_leads(ID) == [lead, lead]
        assert await tx.has_manual_transition(ID) is True
        assert await tx.has_manual_transition(ID) is False
    assert "core.processed_event" in engine.connection.calls[0][0]


async def test_outbox_transaction_sql_paths() -> None:
    event: dict[str, object] = {
        "id": 5,
        "event_id": ID,
        "tenant_id": TENANT,
        "event_type": "x",
        "payload": {"p": NOW},
        "replay": None,
    }
    unit, engine = _uow(repo.SqlOutboxUnitOfWork, [FakeResult([event])])
    async with unit as tx:
        assert await tx.claim_due(NOW, 10) == [event]
        await tx.mark_published(5, NOW)
        await tx.record_failure(5, attempts=2, next_attempt_at=NOW, error="e")
        await tx.dead_letter(event, attempts=3, error="e", now=NOW)
    calls = engine.connection.calls
    assert "SKIP LOCKED" in calls[0][0]
    assert calls[0][1] == {"now": NOW, "limit": 10}
    assert calls[2][1]["attempts"] == 2
    assert "outbox_dead_letter" in calls[3][0]
    assert calls[3][1]["id"] == 5
    assert "dead_lettered_at" in calls[4][0]


async def test_import_transaction_sql_paths() -> None:
    job: dict[str, object] = {
        "job_id": ID,
        "tenant_id": TENANT,
        "upload_id": ID,
        "entity": "company",
        "preserve_blob": False,
    }
    unit, engine = _uow(repo.SqlImportUnitOfWork)
    conn = engine.connection
    conn.route("FROM core.import_job j JOIN", FakeResult([{"job_id": ID}]))
    conn.route("SELECT count(*)", FakeResult(scalar=3))
    conn.route("SELECT job_id, entity, status", FakeResult([{"job_id": ID}]))
    conn.route("SET status = 'running'", FakeResult([job]), FakeResult())
    conn.route("SELECT content FROM", FakeResult(scalar=bytearray(b"csv")))
    conn.route("INSERT INTO core.import_row", FakeResult(scalar=7))
    async with unit as tx:
        async with tx.row_scope():
            pass
        await tx.lock_key("k")
        assert await tx.find_job_by_key(TENANT, "k") == {"job_id": ID}
        assert await tx.find_job_by_key(TENANT, "k2") is None
        assert await tx.count_active_jobs(TENANT) == 3
        await tx.insert_upload_and_job({"upload_id": ID})
        assert await tx.get_job(TENANT, ID) == {"job_id": ID}
        assert await tx.get_job(None, ID) is None
        claimed = await tx.claim_next_job(NOW)
        assert claimed is not None
        assert claimed["content"] == b"csv"
        assert await tx.claim_next_job(NOW) is None
        assert await tx.insert_row(job, 1, "sha", {"a": 1}) is True
        assert await tx.insert_row(job, 2, "sha", {"a": 1}) is False
        await tx.set_row_status(ID, 1, "done")
        await tx.record_error_row(job, 3, "sha", {"a": 1}, "bad")
        await tx.add_progress(ID, {"processed": 1, "upserted": 1, "duplicates": 0, "errors": 0})
        await tx.finish_job(job, status="done", error=None, now=NOW)
        await tx.finish_job({**job, "preserve_blob": True}, status="done", error=None, now=NOW)
    sql = engine.connection.sql
    assert engine.connection.nested == 1
    assert "''::bytea" in sql
    assert sql.count("''::bytea") == 1


async def test_retention_transaction_sql_paths() -> None:
    unit, engine = _uow(
        repo.SqlRetentionUnitOfWork,
        [
            FakeResult(tuples=[(ID,), (TENANT,)]),
            FakeResult(scalar=ID),
            *[FakeResult(rowcount=i) for i in range(8)],
        ],
    )
    async with unit as tx:
        assert await tx.find_purge_candidates(NOW, 5) == [ID, TENANT]
        assert await tx.find_company(ID, None) == ID
        counts = await tx.delete_company_data(ID)
        await tx.add_audit(action="compliance.purge", payload={"at": NOW})
    assert list(counts) == [
        "lead_outcome",
        "lead_activity",
        "lead",
        "signal",
        "person_role",
        "location",
        "audit_log",
        "company",
    ]
    assert counts["company"] == 7
    assert "compliance" in engine.connection.calls[-1][0]


def _repository(results: list[FakeResult] | None = None) -> tuple[repo.CoreRepository, FakeEngine]:
    repository = repo.CoreRepository(None)
    engine = FakeEngine(results)
    repository.engine = cast(AsyncEngine, engine)
    return repository, engine


async def test_repository_without_engine_raises_everywhere() -> None:
    repository = repo.CoreRepository(None)
    assert repository.engine is None
    factories = [
        repository.company_unit_of_work,
        repository.lead_unit_of_work,
        repository.action_unit_of_work,
        repository.config_unit_of_work,
        repository.qualification_unit_of_work,
        repository.outbox_unit_of_work,
        repository.import_unit_of_work,
        repository.retention_unit_of_work,
    ]
    for factory in factories:
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            factory()
    signal = SignalCreate(company_id=ID, source="s", signal_type="t", observed_at=NOW)
    coroutines = [
        repository.list_outbox_events_since(NOW, None, 0, 1),
        repository.create_signal(signal, request_id=None),
        repository.list_companies(1),
        repository.list_leads(1),
        repository.get_lead(ID),
        repository.list_projects(1),
    ]
    for coroutine in coroutines:
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            await coroutine
    await repository.close()


def test_repository_unit_of_work_factories_with_engine() -> None:
    repository, _ = _repository()
    assert isinstance(repository.company_unit_of_work(), repo.SqlCompanyUnitOfWork)
    assert isinstance(repository.lead_unit_of_work(), repo.SqlLeadUnitOfWork)
    assert isinstance(repository.action_unit_of_work(), repo.SqlActionUnitOfWork)
    assert isinstance(repository.config_unit_of_work(), repo.SqlConfigUnitOfWork)
    assert isinstance(repository.qualification_unit_of_work(), repo.SqlQualificationUnitOfWork)
    assert isinstance(repository.outbox_unit_of_work(), repo.SqlOutboxUnitOfWork)
    assert isinstance(repository.import_unit_of_work(), repo.SqlImportUnitOfWork)
    assert isinstance(repository.retention_unit_of_work(), repo.SqlRetentionUnitOfWork)


async def test_repository_close_disposes_engine() -> None:
    repository, engine = _repository()
    await repository.close()
    assert engine.disposed is True


async def test_list_outbox_events_since_filters_by_company() -> None:
    repository, engine = _repository([FakeResult([{"id": 1}])])
    rows = await repository.list_outbox_events_since(NOW, ID, 4, 10)
    assert rows == [{"id": 1}]
    assert engine.connection.calls[0][1] == {
        "since": NOW,
        "after_id": 4,
        "company_id": str(ID),
        "limit": 10,
    }
    await repository.list_outbox_events_since(NOW, None, 0, 1)
    assert engine.connection.calls[1][1]["company_id"] is None


def _signal(**overrides: Any) -> SignalCreate:
    values: dict[str, Any] = {
        "company_id": ID,
        "source": "rues",
        "signal_type": "hiring",
        "observed_at": NOW,
        "strength": 0.7,
        "payload": {"k": "v"},
    }
    return SignalCreate(**{**values, **overrides})


async def test_create_signal_inserts_signal_and_outbox_event() -> None:
    signal_id = uuid4()
    repository, engine = _repository([FakeResult(scalar=signal_id), FakeResult()])
    returned_id, event_id = await repository.create_signal(_signal(), request_id="req-1")
    assert returned_id == signal_id
    assert event_id is not None
    calls = engine.connection.calls
    assert len(calls) == 2
    assert "signal.created" in calls[1][0]
    body = json.loads(calls[1][1]["payload"])
    assert body["request_id"] == "req-1"
    assert body["source_external_id"] == calls[0][1]["source_external_id"]
    assert engine.log == ["enter:begin", "exit:begin:ok"]


async def test_create_signal_duplicate_returns_existing_without_event() -> None:
    existing = uuid4()
    repository, engine = _repository([FakeResult(scalar=None), FakeResult(scalar=existing)])
    returned_id, event_id = await repository.create_signal(
        _signal(source_external_id="ext-1"), request_id=None
    )
    assert (returned_id, event_id) == (existing, None)
    assert len(engine.connection.calls) == 2
    assert engine.connection.calls[0][1]["source_external_id"] == "ext-1"


def test_derived_external_id_is_deterministic_and_input_sensitive() -> None:
    first = repo.CoreRepository._derived_external_id(_signal())
    assert first == repo.CoreRepository._derived_external_id(_signal())
    assert len(first) == 64
    assert first != repo.CoreRepository._derived_external_id(_signal(strength=0.8))
    assert first != repo.CoreRepository._derived_external_id(_signal(payload={"k": "w"}))


def _paged_rows(count: int, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [
        {"id": uuid4(), "updated_at": NOW - timedelta(minutes=i), **(extra or {})}
        for i in range(count)
    ]


async def test_list_companies_paginates_with_cursor() -> None:
    rows = _paged_rows(3)
    repository, engine = _repository([FakeResult(rows)])
    page, next_cursor = await repository.list_companies(2)
    assert page == rows[:2]
    assert next_cursor == encode_cursor(rows[1]["updated_at"], rows[1]["id"])
    assert engine.connection.calls[0][1]["limit"] == 3
    assert "WHERE" not in engine.connection.calls[0][0]

    repository, engine = _repository([FakeResult(rows[:1])])
    page, next_cursor = await repository.list_companies(2, next_cursor)
    assert next_cursor is None
    assert "cursor_updated_at" in engine.connection.calls[0][1]
    assert "(updated_at, id) <" in engine.connection.calls[0][0]


async def test_list_companies_rejects_invalid_cursor_and_rows() -> None:
    repository, _ = _repository()
    with pytest.raises(ValueError, match="invalid_cursor"):
        await repository.list_companies(2, "!!!")
    repository, _ = _repository([FakeResult([{"id": "x", "updated_at": "y"}] * 2)])
    with pytest.raises(RuntimeError, match="company cursor"):
        await repository.list_companies(1)


async def test_list_leads_applies_all_filters() -> None:
    rows = _paged_rows(2)
    repository, engine = _repository([FakeResult(rows)])
    cursor = encode_cursor(NOW, ID)
    page, next_cursor = await repository.list_leads(
        1,
        cursor,
        stage="hot",
        service_line="web",
        city="Cali",
        segment="6201",
        min_score=50,
        tenant_id=TENANT,
    )
    assert len(page) == 1
    assert next_cursor is not None
    sql, params = engine.connection.calls[0]
    for clause in ("l.stage = :stage", "s.score >= :min_score", "l.tenant_id = :tenant_id"):
        assert clause in sql
    assert params["stage"] == "hot"
    assert params["min_score"] == 50
    assert params["cursor_id"] == ID


async def test_list_leads_without_filters_and_invalid_rows() -> None:
    repository, engine = _repository([FakeResult([])])
    assert await repository.list_leads(5) == ([], None)
    assert "WHERE" not in engine.connection.calls[0][0]
    repository, _ = _repository([FakeResult([{"id": 1, "updated_at": NOW}] * 2)])
    with pytest.raises(RuntimeError, match="lead cursor"):
        await repository.list_leads(1)


async def test_get_lead_returns_row_or_none() -> None:
    repository, engine = _repository([FakeResult([{"id": ID}])])
    assert await repository.get_lead(ID, TENANT) == {"id": ID}
    assert await repository.get_lead(ID) is None
    assert engine.connection.calls[0][1] == {"id": ID, "tenant_id": TENANT}


async def test_list_projects_filters_and_paginates() -> None:
    rows = _paged_rows(2)
    repository, engine = _repository([FakeResult(rows)])
    page, next_cursor = await repository.list_projects(
        1, encode_cursor(NOW, ID), status="open", tenant_id=TENANT
    )
    assert len(page) == 1
    assert next_cursor is not None
    sql, params = engine.connection.calls[0]
    assert "status = :status" in sql
    assert "tenant_id = :tenant_id" in sql
    assert params["status"] == "open"
    repository, _ = _repository([FakeResult([{"id": 1, "updated_at": NOW}] * 2)])
    with pytest.raises(RuntimeError, match="project cursor"):
        await repository.list_projects(1)


def test_fakes_are_consistent() -> None:
    assert FakeConnection().results == []
    assert isinstance(ID, UUID)
