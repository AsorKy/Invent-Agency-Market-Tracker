"""Offline proof that the demo seed fills scores and briefs (T-141, REQ-RUN-001, REQ-QA-001).

The seed loads demo companies through core's real internal API, then ``ops/seed/enrich.py`` posts
score features to ML and reads Brief digests, all over ``support_pipeline.World`` (in-memory
repositories, fake Redis, real service apps). Afterwards the outbox relay, the Pulse consumer and
the core worker run, and the outbox must be drained with no dead letter, the condition the CI e2e
stage checks on Postgres. The CI e2e stage is the real check; this test only proves the wiring.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("fakeredis")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")

import support_pipeline  # noqa: E402
from support_pipeline import (  # noqa: E402
    EVENTS_STREAM,
    TENANT_ID,
    ScriptlessRedis,
    World,
)

from ops.seed import __main__ as seed_cli  # noqa: E402
from ops.seed import enrich, loader  # noqa: E402
from services.brief.app import main as brief_main  # noqa: E402

SMALL = {"orgs": 6, "days": 30, "seed": 7}


class SeedHttpCore(loader.HttpSeedCore):
    """The real seed adapter over an injected client (an ASGI transport)."""


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def world() -> AsyncIterator[World]:
    redis = ScriptlessRedis(decode_responses=True)
    stack = World(redis)
    try:
        yield stack
    finally:
        await stack.close()
        await redis.aclose()


def _clients(world: World) -> tuple[Any, Any, Any]:
    settings = brief_main.settings
    core = httpx.AsyncClient(transport=world.transport, base_url=settings.core_url)
    ml = httpx.AsyncClient(transport=world.transport, base_url=settings.ml_url)
    brief = httpx.AsyncClient(transport=world.transport, base_url=settings.brief_url)
    return core, ml, brief


async def _seed(world: World) -> tuple[loader.SeedResult, enrich.EnrichResult]:
    core_client, ml_client, brief_client = _clients(world)
    settings = brief_main.settings
    core = SeedHttpCore(settings.core_url, settings.internal_service_token, client=core_client)
    port = enrich.HttpScoreBrief(
        settings.ml_url,
        settings.brief_url,
        settings.internal_service_token,
        loader.DEMO_TENANT_ID,
        ml_client=ml_client,
        brief_client=brief_client,
    )
    try:
        loaded = await loader.load(core, **SMALL)
        outcome = await enrich.enrich(port, loaded.profiles, sleep=_no_sleep)
    finally:
        await port.aclose()
        await core.aclose()
    return loaded, outcome


async def test_seed_scores_and_briefs_every_demo_company(world: World) -> None:
    assert loader.DEMO_TENANT_ID == TENANT_ID
    loaded, outcome = await _seed(world)

    assert loaded.failed == 0 and len(loaded.profiles) == SMALL["orgs"]
    assert (outcome.scored, outcome.briefed, outcome.failed) == (SMALL["orgs"],) * 2 + (0,)
    store = world.core_store
    assert {str(company_id) for company_id in store.scores} == set(loaded.profiles)
    assert len(store.score_history) == SMALL["orgs"]
    assert all(0 <= row["score"] <= 100 for row in store.scores.values())
    records = brief_main.app.state.brief.repository._records
    assert {str(company_id) for company_id in records} == set(loaded.profiles)
    assert all(digest.content for digest in records.values())


async def test_scores_reach_core_and_the_outbox_drains_without_dead_letters(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The shared fakes drop two things the real repositories keep: the ml features and the core
    # company score. Record both here, as the CI e2e check requires them in Postgres.
    features: dict[Any, tuple[float, float, float] | None] = {}
    ml_repository = world.ml_app.state.repository
    store_in_ml = ml_repository.upsert_score

    async def keep_features(**kwargs: Any) -> None:
        features[kwargs["company_id"]] = kwargs.get("features")
        await store_in_ml(**kwargs)

    monkeypatch.setattr(ml_repository, "upsert_score", keep_features)

    async def store_company_score(*, company_id: Any, score: int, **_rest: Any) -> bool:
        world.core_store.companies[company_id]["score"] = score
        return True

    real_build = support_pipeline.build_handlers
    monkeypatch.setattr(
        support_pipeline,
        "build_handlers",
        lambda factory: real_build(factory, store_score=store_company_score),
    )
    loaded, _ = await _seed(world)

    relayed = await world.run_relay()
    assert relayed["dead_lettered"] == 0 and relayed["retried"] == 0
    await world.run_pulse_consumer()
    await world.run_core_worker()
    again = await world.run_relay()
    assert again["dead_lettered"] == 0 and again["retried"] == 0

    store = world.core_store
    assert store.dead_letters == []
    assert all(row["published_at"] for row in store.outbox)
    assert store.leads, "auto-qualification turns score events into leads"
    assert {lead["company_id"] for lead in store.leads.values()} <= {
        company for company in store.companies
    }
    for company_id, company in store.companies.items():
        assert company["score"] == store.scores[company_id]["score"]
    assert len(store.companies) == len(loaded.profiles)
    assert len(features) == len(loaded.profiles)
    for stored in features.values():
        assert stored is not None and len(stored) == 3
        assert all(0 <= value <= 1 for value in stored)
    entries = await world.redis.xrange(EVENTS_STREAM)
    scored = [fields for _id, fields in entries if fields["event_type"] == "lead.score_updated"]
    assert len(scored) == len(loaded.profiles)
    assert {fields["tenant_id"] for fields in scored} == {str(TENANT_ID)}


async def test_features_are_bounded_and_deterministic() -> None:
    profiles = {
        "b": enrich.CompanyProfile(city="Cali", has_website=True, has_nit=True, signals=40),
        "a": enrich.CompanyProfile(city="Cali"),
        "c": enrich.CompanyProfile(city=None, signals=2),
    }
    rows = enrich.score_features(profiles)

    assert [row["company_id"] for row in rows] == ["a", "b", "c"]
    for row in rows:
        for key in ("icp_fit", "signal_strength", "territory_opportunity"):
            assert 0 <= row[key] <= 1
    assert rows[1]["signal_strength"] == 1.0 and rows[1]["icp_fit"] == 1.0
    assert rows == enrich.score_features(profiles)


async def test_seed_features_match_the_pinned_baseline_rows() -> None:
    from tests.unit.test_score_features import PINNED_PROFILES, PINNED_ROWS

    rows = enrich.score_features(
        {key: enrich.CompanyProfile(**vars(p)) for key, p in PINNED_PROFILES.items()}
    )
    assert rows == PINNED_ROWS[enrich.SCORE_SOURCE_VERSION]


class FlakyPort:
    """Fails with 503 once, then answers; brief for ``bad`` always answers 404."""

    def __init__(self) -> None:
        self.scored: list[str] = []
        self.calls = 0

    def _status(self, code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "http://x")
        return httpx.HTTPStatusError("x", request=request, response=httpx.Response(code))

    async def score(self, features: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            raise self._status(503)
        self.scored.append(str(features["company_id"]))
        return {}

    async def brief(self, company_id: str) -> dict[str, Any]:
        if company_id == "bad":
            raise self._status(404)
        return {}


async def test_enrich_retries_server_errors_and_counts_failures() -> None:
    port = FlakyPort()
    profiles = {"bad": enrich.CompanyProfile(), "good": enrich.CompanyProfile()}

    outcome = await enrich.enrich(port, profiles, sleep=_no_sleep)

    assert (outcome.scored, outcome.briefed, outcome.failed) == (2, 1, 1)
    assert outcome.reasons == {"brief: HTTPStatusError HTTP 404": 1}


async def test_cli_enriches_after_load_and_reports_failures(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    core_client, _, _ = _clients(world)
    core = SeedHttpCore(
        brief_main.settings.core_url,
        brief_main.settings.internal_service_token,
        client=core_client,
    )
    port = FlakyPort()
    try:
        code = await seed_cli.run(
            ["load", "--orgs", "3", "--days", "30", "--seed", "7"], core=core, scorer=port
        )
    finally:
        await core.aclose()

    assert code == 0 and len(port.scored) == 3
    assert "seed enrich: scored=3 briefed=3 failed=0" in capsys.readouterr().out
