"""Failure matrix tests for REQ-RES-002 (ADR-IMT-0001 section E1.10).

Each case injects one fault with a fake and checks what keeps working:

* ``ml`` down: Pulse still answers and the Brief is served ``degraded``.
* ``pulse`` down: the gateway answers 503 for Pulse and the Brief still works.
* ``brief`` down: the Brief panel answers 503 and the rest of the lead page works.
* Redis down: Postgres-backed reads and writes work, the rate limiter stays open,
  and duplicate Brief regeneration is tolerated.

The gateway runs in-process. Its upstream HTTP client is replaced by a transport
that dispatches by port to the real Pulse and Brief ASGI apps (with in-memory
fakes behind them) and to small fakes for core and ml, so no network, Redis, or
Postgres is used. The module skips when runtime dependencies are absent.
"""

import asyncio
import dataclasses
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
pytest.importorskip("fakeredis")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")
redis_exceptions = pytest.importorskip("redis.exceptions")

from libs.common.settings import get_settings  # noqa: E402
from services.brief.app import main as brief_main  # noqa: E402
from services.brief.app.core_source import CoreUnavailable  # noqa: E402
from services.brief.app.models import BriefRules, CompanyFacts  # noqa: E402
from services.brief.app.repository import BriefRepository  # noqa: E402
from services.brief.app.rule_based import SummaryResult, build_rule_based_content  # noqa: E402
from services.brief.app.service import BriefService  # noqa: E402
from services.brief.app.single_flight import SingleFlightLock  # noqa: E402
from services.gateway.app import main as gw  # noqa: E402
from services.pulse.app import main as pulse_main  # noqa: E402
from services.pulse.app.models import PipelineBacklog  # noqa: E402
from tests.unit.support_brief import COMPANY, TENANT, FakeCore, FakeRedis  # noqa: E402

REAL_ASYNC_CLIENT = httpx.AsyncClient
SERVICE_HEADERS = {"X-Service-Token": get_settings().internal_service_token}
ML_PORT = httpx.URL(get_settings().ml_url).port
PULSE_PORT = httpx.URL(get_settings().pulse_url).port
BRIEF_PORT = httpx.URL(get_settings().brief_url).port
CORE_PORT = httpx.URL(get_settings().core_url).port
LEAD = UUID("00000000-0000-4000-8000-0000000000a1")


# --- fakes -------------------------------------------------------------------


class PulseRepo:
    """Serve a fixed backlog and fail with a database error when ``down``."""

    def __init__(self) -> None:
        self.down = False

    async def pipeline_backlog(self, **_: Any) -> tuple[PipelineBacklog, datetime]:
        if self.down:
            raise RuntimeError("db_unavailable")
        return PipelineBacklog(active=41, hot=7, capacity_estimate=50), datetime.now(
            UTC
        ) - timedelta(hours=1)

    async def market_overview(self, **_: Any) -> dict[str, object]:
        if self.down:
            raise RuntimeError("db_unavailable")
        return {"total": 3}


class BrokenRedis:
    """Redis double whose every command fails like a lost connection."""

    async def set(self, *_: Any, **__: Any) -> bool:
        raise redis_exceptions.ConnectionError("redis down")

    async def eval(self, *_: Any, **__: Any) -> int:
        raise redis_exceptions.ConnectionError("redis down")


class MlSummarizer:
    """Call the (fake) ml service over HTTP; any failure propagates to the Brief."""

    def __init__(self, ml: Callable[[], Any]) -> None:
        self.ml = ml
        self.calls = 0

    async def summarize(self, facts: CompanyFacts, rules: BriefRules) -> SummaryResult:
        self.calls += 1
        response = await self.ml()
        response.raise_for_status()
        return SummaryResult(build_rule_based_content(facts, rules), "ml-v1")


class Mesh:
    """Dispatch gateway upstream calls by port and let tests take services down."""

    def __init__(self) -> None:
        self.down: set[int | None] = set()
        self.core_requests: list[str] = []
        self.transports = {
            PULSE_PORT: httpx.ASGITransport(app=pulse_main.app),
            BRIEF_PORT: httpx.ASGITransport(app=brief_main.app),
        }

    async def handle(self, request: Any) -> Any:
        port = request.url.port
        if port in self.down:
            raise httpx.ConnectError("service down", request=request)
        if port in self.transports:
            return await self.transports[port].handle_async_request(request)
        if port == CORE_PORT:
            self.core_requests.append(request.url.path)
            return httpx.Response(200, json={"id": str(LEAD), "stage": "new"})
        if port == ML_PORT:
            return httpx.Response(200, json={"score": 80})
        return httpx.Response(404)


class MeshTransport(httpx.AsyncBaseTransport):
    def __init__(self, mesh: Mesh) -> None:
        self.mesh = mesh

    async def handle_async_request(self, request: Any) -> Any:
        return await self.mesh.handle(request)


def _brief_state(
    redis: Any, core: FakeCore, summarizer: Any = None, **kw: float
) -> SimpleNamespace:
    repository = BriefRepository(None)
    service = BriefService(
        repository=repository,
        lock=SingleFlightLock(redis, ttl_ms=30_000),
        core=core,
        summarizer=summarizer,
        **kw,
    )
    return SimpleNamespace(repository=repository, redis=redis, core=core, service=service)


class StubLimiter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def check(self, identity: str) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(allowed=True, limit=5, remaining=4, retry_after_seconds=None)


@dataclasses.dataclass
class World:
    client: Any
    mesh: Mesh
    pulse_repo: PulseRepo
    core: FakeCore
    summarizer: MlSummarizer
    limiter: StubLimiter


@pytest.fixture
async def world(monkeypatch: pytest.MonkeyPatch, asgi_client: Any) -> AsyncIterator[World]:
    mesh = Mesh()

    def factory(*args: Any, **kwargs: Any) -> Any:
        return REAL_ASYNC_CLIENT(*args, transport=MeshTransport(mesh), **kwargs)

    shim = SimpleNamespace(
        AsyncClient=factory,
        HTTPError=httpx.HTTPError,
        TransportError=httpx.TransportError,
        Response=httpx.Response,
    )
    monkeypatch.setattr(gw, "httpx", shim)
    monkeypatch.setattr(
        gw,
        "settings",
        gw.settings.model_copy(
            update={
                "upstream_retry_base_delay_seconds": 0,
                "upstream_retry_max_delay_seconds": 0,
                "upstream_retry_attempts": 2,
            }
        ),
    )

    async def ml_call() -> Any:
        async with REAL_ASYNC_CLIENT(transport=MeshTransport(mesh)) as http:
            return await http.get(f"{get_settings().ml_url}/internal/ml/scores/summarize")

    pulse_repo = PulseRepo()
    core = FakeCore(CompanyFacts(company_id=COMPANY, version=1, sector="saas", city="Bogota"))
    summarizer = MlSummarizer(ml_call)
    pulse_main.app.state.repository = pulse_repo
    brief_main.app.state.brief = _brief_state(FakeRedis(), core, summarizer, wait_interval=0.001)
    limiter = StubLimiter()
    async with asgi_client(gw.app) as http:
        gw.app.state.rate_limiter = limiter
        yield World(http, mesh, pulse_repo, core, summarizer, limiter)


def _lead_brief_path(lead: UUID = LEAD) -> str:
    return f"/api/v1/leads/{lead}/brief"


# --- ml down -----------------------------------------------------------------


async def test_ml_down_pulse_works_and_brief_is_degraded(world: World) -> None:
    world.mesh.down.add(ML_PORT)

    pulse = await world.client.get("/api/v1/pulse/pipeline-backlog")
    brief = await world.client.get(_lead_brief_path(), params={"x": "1"})

    assert pulse.status_code == 200
    assert pulse.json()["active"] == 41
    assert brief.status_code == 200
    body = brief.json()
    assert body["degraded"] is True
    assert brief.headers["x-brief-cache"] == "degraded"
    assert body["content"]["headline"]
    assert world.summarizer.calls == 1


async def test_ml_down_scores_route_is_503_without_affecting_others(world: World) -> None:
    world.mesh.down.add(ML_PORT)

    scores = await world.client.get("/api/v1/scores/leads")
    pulse = await world.client.get("/api/v1/pulse/pipeline-backlog")

    assert scores.status_code == 503
    assert scores.json() == {"detail": "upstream_unavailable"}
    assert pulse.status_code == 200


async def test_ml_error_status_degrades_brief_and_recovery_is_not_degraded(
    world: World,
) -> None:
    async def failing() -> Any:
        return httpx.Response(500, request=httpx.Request("GET", "http://ml"))

    world.summarizer.ml = failing
    first = await world.client.get(_lead_brief_path())
    assert first.status_code == 200
    assert first.json()["degraded"] is True

    # A new source version makes the degraded digest stale; with ml back it is replaced.
    async def healthy() -> Any:
        return httpx.Response(200, request=httpx.Request("GET", "http://ml"))

    world.summarizer.ml = healthy
    world.core.facts = world.core.facts.model_copy(update={"version": 2})
    stale = await world.client.get(_lead_brief_path())
    assert stale.headers["x-brief-cache"] == "stale"
    await asyncio.sleep(0.05)
    fresh = await world.client.get(_lead_brief_path())
    assert fresh.json()["degraded"] is False
    assert fresh.headers["x-brief-cache"] == "hit"


# --- pulse down --------------------------------------------------------------


async def test_pulse_down_gateway_503_and_brief_still_works(world: World) -> None:
    world.mesh.down.add(PULSE_PORT)

    pulse = await world.client.get("/api/v1/pulse/pipeline-backlog")
    brief = await world.client.get(_lead_brief_path())

    assert pulse.status_code == 503
    assert pulse.json() == {"detail": "upstream_unavailable"}
    assert brief.status_code == 200
    assert brief.json()["degraded"] is False


async def test_pulse_database_error_is_a_503_card_and_brief_still_works(world: World) -> None:
    world.pulse_repo.down = True

    market = await world.client.get("/api/v1/market")
    brief = await world.client.get(_lead_brief_path())

    assert market.status_code == 503
    assert brief.status_code == 200


async def test_pulse_marked_red_by_readiness_is_503_and_brief_unaffected(
    world: World,
) -> None:
    gw.readiness.state["pulse"] = False
    try:
        pulse = await world.client.get("/api/v1/pulse/pipeline-backlog")
        brief = await world.client.get(_lead_brief_path())
    finally:
        gw.readiness.state.clear()

    assert pulse.status_code == 503
    assert brief.status_code == 200


# --- brief down --------------------------------------------------------------


async def test_brief_down_panel_503_and_rest_of_lead_page_works(world: World) -> None:
    world.mesh.down.add(BRIEF_PORT)

    panel = await world.client.get(_lead_brief_path())
    lead = await world.client.get(f"/api/v1/leads/{LEAD}")
    pulse = await world.client.get("/api/v1/pulse/pipeline-backlog")

    assert panel.status_code == 503
    assert panel.json() == {"detail": "upstream_unavailable"}
    assert lead.status_code == 200
    assert lead.json()["id"] == str(LEAD)
    assert pulse.status_code == 200
    assert f"/internal/core/leads/{LEAD}" in world.mesh.core_requests


async def test_brief_core_outage_answers_503_db_unavailable(world: World) -> None:
    world.core.down = True

    panel = await world.client.get(_lead_brief_path())

    assert panel.status_code == 503
    assert panel.json() == {"detail": "db_unavailable"}


async def test_brief_marked_red_by_readiness_leaves_lead_page_working(world: World) -> None:
    gw.readiness.state["brief"] = False
    try:
        panel = await world.client.get(_lead_brief_path())
        lead = await world.client.get(f"/api/v1/leads/{LEAD}")
    finally:
        gw.readiness.state.clear()

    assert panel.status_code == 503
    assert lead.status_code == 200


# --- Redis down --------------------------------------------------------------


async def test_redis_down_rate_limiter_is_open_and_requests_proceed(world: World) -> None:
    world.limiter.error = redis_exceptions.ConnectionError("redis down")

    pulse = await world.client.get("/api/v1/pulse/pipeline-backlog")
    lead = await world.client.get(f"/api/v1/leads/{LEAD}")
    write = await world.client.post(f"/api/v1/leads/{LEAD}/notes", json={"text": "x"})

    assert world.limiter.calls == 3
    assert pulse.status_code == 200
    assert lead.status_code == 200
    assert write.status_code == 200


async def test_redis_down_real_limiter_fails_open(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from libs.common.rate_limiter import RedisSlidingWindowRateLimiter

    gw.app.state.rate_limiter = RedisSlidingWindowRateLimiter(
        BrokenRedis(),  # type: ignore[arg-type]
        limit=1,
        window_seconds=60,
    )

    first = await world.client.get(f"/api/v1/leads/{LEAD}")
    second = await world.client.get(f"/api/v1/leads/{LEAD}")

    assert first.status_code == 200
    assert second.status_code == 200


async def test_redis_down_limiter_closes_only_when_fail_open_is_disabled(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.limiter.error = redis_exceptions.ConnectionError("redis down")
    monkeypatch.setattr(
        gw, "settings", gw.settings.model_copy(update={"rate_limit_fail_open": False})
    )

    response = await world.client.get(f"/api/v1/leads/{LEAD}")

    assert response.status_code == 503
    assert response.json() == {"detail": "rate_limiter_unavailable"}


async def test_redis_down_brief_still_reads_and_writes_the_digest_store(world: World) -> None:
    brief_main.app.state.brief = _brief_state(BrokenRedis(), world.core, world.summarizer)

    first = await world.client.get(_lead_brief_path())
    second = await world.client.get(_lead_brief_path())

    assert first.status_code == 200
    assert first.json()["degraded"] is False
    assert second.status_code == 200
    assert second.headers["x-brief-cache"] == "hit"
    assert first.json()["generated_at"] == second.json()["generated_at"]


async def test_redis_down_duplicate_regeneration_is_tolerated(world: World) -> None:
    gate = asyncio.Event()

    class GatedSummarizer:
        calls = 0

        async def summarize(self, facts: CompanyFacts, rules: BriefRules) -> SummaryResult:
            GatedSummarizer.calls += 1
            await gate.wait()
            return SummaryResult(build_rule_based_content(facts, rules), "llm-v1")

    state = _brief_state(BrokenRedis(), world.core, GatedSummarizer(), wait_interval=0.001)
    readers = [asyncio.create_task(state.service.read(COMPANY, TENANT)) for _ in range(3)]
    await asyncio.sleep(0.05)
    gate.set()
    results = await asyncio.gather(*readers)

    # Without a lock every reader regenerates, but all of them get a valid digest
    # and the store keeps exactly one latest record.
    assert GatedSummarizer.calls == 3
    assert all(not result.digest.degraded for result, _ in results)
    stored = await state.repository.get_latest(COMPANY)
    assert stored is not None
    assert stored.source_version == 1


async def test_redis_down_brief_core_failure_still_maps_to_503(world: World) -> None:
    brief_main.app.state.brief = _brief_state(BrokenRedis(), world.core, world.summarizer)
    world.core.down = True

    response = await world.client.get(_lead_brief_path())

    assert response.status_code == 503
    assert response.json() == {"detail": "db_unavailable"}
    with pytest.raises(CoreUnavailable):
        await world.core.get_facts(COMPANY, None)


async def test_redis_down_pulse_reads_do_not_touch_redis(world: World) -> None:
    world.limiter.error = redis_exceptions.ConnectionError("redis down")

    response = await world.client.get("/api/v1/pulse/pipeline-backlog")

    assert response.status_code == 200
    assert response.json()["hot"] == 7
