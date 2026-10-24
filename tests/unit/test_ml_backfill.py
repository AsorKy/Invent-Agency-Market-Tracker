"""The ML feature backfill (T-165, REQ-LNS-007, INV-01)."""

from uuid import UUID, uuid4

import pytest

from libs.domain.scoring import ScoreWeights
from services.ml.app import backfill
from services.ml.app.core_features import (
    CompanyFeatures,
    CoreFeaturesPage,
    CoreFeaturesUnavailable,
    HttpCoreFeatures,
)
from services.ml.app.scoring_service import ScoringService
from tests.unit.test_ml_singleflight import FakeRedis, FakeStore

TENANT = uuid4()
WEIGHTS = ScoreWeights()


def _company(company_id: UUID) -> CompanyFeatures:
    return CompanyFeatures(
        company_id=company_id,
        icp_fit=0.7,
        signal_strength=0.5,
        territory_opportunity=1.0,
        source_version="demo-1",
    )


class Core:
    """Serves ``ids`` in pages of the requested size; records every request."""

    def __init__(self, ids: list[UUID]) -> None:
        self.ids = sorted(ids)
        self.requests: list[tuple[UUID, int, UUID | None]] = []

    async def get_page(self, tenant_id, limit, after) -> CoreFeaturesPage:
        self.requests.append((tenant_id, limit, after))
        rest = [i for i in self.ids if after is None or i > after]
        page = rest[:limit]
        more = len(rest) > limit
        return CoreFeaturesPage([_company(i) for i in page], page[-1] if more else None)

    async def close(self) -> None:
        return None


class Repo:
    """ml rows: ``old`` have null features, ``new`` have features; others are other tenants'."""

    def __init__(self, old: set[UUID], new: set[UUID] = frozenset()) -> None:  # type: ignore[assignment]
        self.old, self.new = set(old), set(new)
        self.asked: list[list[UUID]] = []

    async def without_features(self, ids: list[UUID]) -> set[UUID]:
        self.asked.append(list(ids))
        return {i for i in ids if i in self.old}


class Store(FakeStore):
    def __init__(self, repo: Repo) -> None:
        super().__init__()
        self.repo = repo
        self.rescored: list[UUID] = []

    async def upsert_score(self, **kw) -> None:  # type: ignore[override]
        self.rescored.append(kw["company_id"])
        assert kw["features"] == (0.7, 0.5, 1.0)
        self.repo.old.discard(kw["company_id"])
        await super().upsert_score(**kw)


def _run(core, repo, limit=100, page_size=2, max_pages=50):
    store = Store(repo)
    service = ScoringService(store, FakeRedis(), lock_ttl_seconds=5)  # type: ignore[arg-type]
    counts = backfill.run_backfill(
        TENANT, limit, core, repo, service, WEIGHTS, page_size=page_size, max_pages=max_pages
    )
    return store, counts


async def test_rescores_only_rows_without_features_and_is_idempotent() -> None:
    ids = [uuid4() for _ in range(5)]
    core = Core(ids)
    repo = Repo(old={ids[0], ids[3]}, new={ids[1]})
    store, pending = _run(core, repo)
    counts = await pending
    assert sorted(store.rescored) == sorted([ids[0], ids[3]])
    assert (counts.examined, counts.missing, counts.rescored, counts.pages) == (5, 2, 2, 3)
    assert {r[0] for r in core.requests} == {TENANT}
    store2, pending2 = _run(core, repo)
    again = await pending2
    assert store2.rescored == [] and again.rescored == 0 and again.missing == 0


async def test_never_rescores_a_company_core_did_not_return() -> None:
    mine, foreign = [uuid4(), uuid4()], [uuid4(), uuid4()]
    core = Core(mine)
    repo = Repo(old=set(mine) | set(foreign))  # ml holds old rows of another tenant too
    store, pending = _run(core, repo)
    await pending
    assert sorted(store.rescored) == sorted(mine)
    assert not set(foreign) & set(store.rescored)
    assert not set(foreign) & {i for ask in repo.asked for i in ask}


class LeakyRepo(Repo):
    async def without_features(self, ids):  # answers ids it was not asked about
        return set(self.old)


async def test_leaky_repository_answer_is_intersected_with_core_ids() -> None:
    mine, foreign = [uuid4()], [uuid4()]
    repo = LeakyRepo(old=set(mine) | set(foreign))
    store, pending = _run(Core(mine), repo)
    await pending
    assert store.rescored == mine


async def test_limit_bounds_the_rescored_rows_and_next_run_continues() -> None:
    ids = [uuid4() for _ in range(6)]
    repo = Repo(old=set(ids))
    core = Core(ids)
    store, pending = _run(core, repo, limit=4, page_size=3)
    counts = await pending
    assert counts.rescored == 4 and len(store.rescored) == 4
    store2, pending2 = _run(core, repo, limit=4, page_size=3)
    assert (await pending2).rescored == 2


async def test_max_pages_bounds_the_loop() -> None:
    ids = [uuid4() for _ in range(10)]
    _, pending = _run(Core(ids), Repo(old=set()), max_pages=2)
    counts = await pending
    assert counts.pages == 2 and counts.examined == 4


async def test_core_unavailable_propagates() -> None:
    class Down(Core):
        async def get_page(self, *_a):
            raise CoreFeaturesUnavailable("down")

    _, pending = _run(Down([]), Repo(old=set()))
    with pytest.raises(CoreFeaturesUnavailable):
        await pending


async def test_a_failed_rescore_is_counted_and_does_not_stop_the_run() -> None:
    ids = [uuid4(), uuid4()]
    repo = Repo(old=set(ids))
    store = Store(repo)
    first = True
    original = store.upsert_score

    async def flaky(**kw):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("db")
        await original(**kw)

    store.upsert_score = flaky  # type: ignore[method-assign]
    service = ScoringService(store, FakeRedis(), lock_ttl_seconds=5)  # type: ignore[arg-type]
    counts = await backfill.run_backfill(TENANT, 10, Core(ids), repo, service, WEIGHTS)
    assert (counts.failed, counts.rescored) == (1, 1)


def test_output_line_has_counts_only() -> None:
    line = backfill.BackfillCounts(pages=1, examined=2, missing=1, rescored=1).line(TENANT)
    assert line == (
        f"backfill tenant={TENANT} pages=1 examined=2 missing=1 rescored=1 skipped=0 failed=0"
    )


def test_cli_requires_tenant_and_a_positive_limit() -> None:
    with pytest.raises(SystemExit):
        backfill.main(["--limit", "5"])
    with pytest.raises(SystemExit):
        backfill.main(["--tenant", str(TENANT), "--limit", "0"])


class TaggedCore(Core):
    def __init__(self, ids: list[UUID], tagged: dict[UUID, UUID]) -> None:
        super().__init__(ids)
        self.tagged = tagged

    async def get_page(self, tenant_id, limit, after) -> CoreFeaturesPage:
        page = await super().get_page(tenant_id, limit, after)
        page.companies = [
            c.model_copy(update={"parent_event_id": self.tagged.get(c.company_id)})
            for c in page.companies
        ]
        return page


async def test_parent_event_id_reaches_score_features_and_stays_empty_without_one() -> None:
    with_id, without = uuid4(), uuid4()
    tagged = uuid4()
    repo = Repo(old={with_id, without})
    seen: dict[UUID, UUID | None] = {}
    store = Store(repo)
    service = ScoringService(store, FakeRedis(), lock_ttl_seconds=5)  # type: ignore[arg-type]
    original = service.regenerate

    async def spy(features, weights, tenant_id):
        seen[features.company_id] = features.parent_event_id
        return await original(features, weights, tenant_id)

    service.regenerate = spy  # type: ignore[method-assign]
    core = TaggedCore([with_id, without], {with_id: tagged})
    counts = await backfill.run_backfill(TENANT, 10, core, repo, service, WEIGHTS)
    assert counts.rescored == 2 and counts.failed == 0
    assert seen == {with_id: tagged, without: None}


async def test_http_core_features_reads_the_parent_event_id() -> None:
    import httpx

    cid, tagged = uuid4(), uuid4()
    body = {
        "companies": [
            {
                "company_id": str(cid),
                "icp_fit": 0.5,
                "signal_strength": 0.5,
                "territory_opportunity": 0.5,
                "source_version": "demo-1",
                "parent_event_id": str(tagged),
            }
        ],
        "next_after": None,
    }
    client = HttpCoreFeatures("http://core", "secret")
    client._client = httpx.AsyncClient(
        base_url="http://core",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    )
    page = await client.get_page(TENANT, 10, None)
    await client.close()
    assert page.companies[0].parent_event_id == tagged
