"""Core score-features route and the shared derivation (T-165, REQ-LNS-007, REQ-SEC-005)."""

from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from libs.domain.score_features import CompanyProfile, score_features  # noqa: E402
from services.core.app import main  # noqa: E402
from services.core.app.score_features import (  # noqa: E402
    MAX_LIMIT,
    build_features_body,
    read_score_features,
)
from tests.unit.service_auth import service_headers  # noqa: E402

TENANT_A = uuid4()
TENANT_B = uuid4()
URL = "/internal/core/admin/score-features"
IDS = sorted(uuid4() for _ in range(5))
PROFILE = CompanyProfile(city="Cali", has_website=True, signals=3)
DATA = {
    TENANT_A: [(IDS[0], PROFILE), (IDS[2], CompanyProfile(city="Cali"))],
    TENANT_B: [(IDS[1], CompanyProfile(city="Bogota"))],
}


class Repo:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int, UUID | None]] = []

    async def score_features(self, tenant_id: UUID, limit: int, after: UUID | None):
        self.calls.append((tenant_id, limit, after))
        rows = [r for r in DATA.get(tenant_id, []) if after is None or r[0] > after]
        counts: dict[str, int] = {}
        for _, p in DATA.get(tenant_id, []):
            counts[p.city or ""] = counts.get(p.city or "", 0) + 1
        return build_features_body(counts, rows[: limit + 1], limit)


@pytest.fixture
def repo():
    stub = Repo()
    main.app.state.repository = stub
    yield stub


def _get(service: str | None, tenant: UUID | None = TENANT_A, **params):
    headers = service_headers(service) if service else {}
    if tenant:
        headers["X-Tenant-Id"] = str(tenant)
    return TestClient(main.app).get(URL, headers=headers, params=params)


def test_seed_output_is_pinned() -> None:
    p = CompanyProfile
    profiles = {
        "c3": p("Cali", True, True, 40),
        "a1": p("Cali"),
        "b2": p(None, False, True, 2),
        "d4": p("Bogota", True, False, 3),
        "e5": p("Cali", False, False, 6),
    }
    third = 1 / 3
    expected = [
        ("a1", 0.4, 0.0, 1.0),
        ("b2", 0.7, third, third),
        ("c3", 1.0, 1.0, 1.0),
        ("d4", 0.7, 0.5, third),
        ("e5", 0.4, 1.0, 1.0),
    ]
    rows = score_features(profiles)
    assert [
        (r["company_id"], r["icp_fit"], r["signal_strength"], r["territory_opportunity"])
        for r in rows
    ] == expected
    assert {r["source_version"] for r in rows} == {"demo-1"}


def test_tenant_a_gets_only_its_companies(repo) -> None:
    body = _get("ml").json()
    assert [c["company_id"] for c in body["companies"]] == [str(IDS[0]), str(IDS[2])]
    assert body["next_after"] is None
    assert body["companies"][0]["signal_strength"] == 0.5
    assert repo.calls == [(TENANT_A, 200, None)]
    other = _get("ml", TENANT_B).json()
    assert [c["company_id"] for c in other["companies"]] == [str(IDS[1])]
    assert _get("ml", uuid4()).json() == {"companies": [], "next_after": None}


def test_paging_with_cursor(repo) -> None:
    first = _get("ml", limit=1).json()
    assert len(first["companies"]) == 1 and first["next_after"] == str(IDS[0])
    second = _get("ml", limit=1, after=first["next_after"]).json()
    assert [c["company_id"] for c in second["companies"]] == [str(IDS[2])]
    assert second["next_after"] is None


def test_territory_uses_the_whole_tenant_not_the_page() -> None:
    counts = {"Cali": 2, "Bogota": 1}
    page = [(IDS[0], CompanyProfile(city="Bogota"))]
    body = build_features_body(counts, page, 5)
    assert body["companies"][0]["territory_opportunity"] == 0.5


def test_tenant_header_is_required(repo) -> None:
    assert _get("ml", None).status_code == 422
    assert repo.calls == []


@pytest.mark.parametrize("service", ["brief", "seed", "gateway"])
def test_other_services_are_refused(repo, service: str) -> None:
    # Same allow list as score-rules: require_service_token answers 401 for other services.
    assert _get(service).status_code == 401
    assert repo.calls == []


def test_unsigned_call_gets_401(repo) -> None:
    assert _get(None).status_code == 401


def test_limit_zero_is_refused_and_large_limit_is_clamped(repo) -> None:
    assert _get("ml", limit=0).status_code == 422
    assert _get("ml", limit=100000).status_code == 200
    assert repo.calls[-1][1] == 100000  # the route passes it; the read clamps it


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Engine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    def begin(self):
        engine = self

        class Conn:
            async def execute(self, statement, params=None):
                engine.calls.append((str(statement), params))
                if "GROUP BY" in str(statement):
                    return _Result([("Cali", 2)])
                if "FROM core.company c" in str(statement):
                    return _Result([(IDS[0], "Cali", True, False, 3)])
                return _Result([])

        class Begin:
            async def __aenter__(self):
                return Conn()

            async def __aexit__(self, *exc):
                return False

        return Begin()


async def test_read_sets_tenant_context_first_and_filters_every_statement() -> None:
    engine = _Engine()
    body = await read_score_features(engine, TENANT_A, 10**6, None)  # type: ignore[arg-type]
    assert "set_config('app.tenant_id'" in engine.calls[0][0]
    for statement, params in engine.calls[1:]:
        assert "tenant_id = :tenant_id" in statement
        assert params and params["tenant_id"] == TENANT_A
    assert engine.calls[2][1]["fetch"] == MAX_LIMIT + 1  # type: ignore[index]
    assert body["companies"][0]["icp_fit"] == pytest.approx(0.7)
    assert body["companies"][0]["territory_opportunity"] == 1.0


# --- T-178 / D-23: versioned baseline and pinned output ---------------------------------------

PINNED_PROFILES = {
    "b": CompanyProfile(city="Cali", has_website=True, has_nit=True, signals=40),
    "a": CompanyProfile(city="Cali"),
    "c": CompanyProfile(city=None, signals=2),
    "d": CompanyProfile(city="Bogota", has_nit=True, signals=3),
    "e": CompanyProfile(city="Cali", has_website=True, signals=6),
}
# Full rows per source_version. A formula change must add a new version with its own rows.
PINNED_ROWS = {
    "demo-1": [
        {
            "company_id": "a",
            "icp_fit": 0.4,
            "signal_strength": 0.0,
            "territory_opportunity": 1.0,
            "source_version": "demo-1",
        },
        {
            "company_id": "b",
            "icp_fit": 1.0,
            "signal_strength": 1.0,
            "territory_opportunity": 1.0,
            "source_version": "demo-1",
        },
        {
            "company_id": "c",
            "icp_fit": 0.4,
            "signal_strength": 2 / 6,
            "territory_opportunity": 1 / 3,
            "source_version": "demo-1",
        },
        {
            "company_id": "d",
            "icp_fit": 0.7,
            "signal_strength": 0.5,
            "territory_opportunity": 1 / 3,
            "source_version": "demo-1",
        },
        {
            "company_id": "e",
            "icp_fit": 0.7,
            "signal_strength": 1.0,
            "territory_opportunity": 1.0,
            "source_version": "demo-1",
        },
    ],
}
# sha256 of the formula source and constants, per source_version.
FORMULA_FINGERPRINTS = {
    "demo-1": "1b2f19a8641f33952850f75126d6a01b7e7282c943a2bf50f1b8918c7373d042",
}


def _formula_fingerprint() -> str:
    import hashlib
    import inspect

    from libs.domain import score_features as module

    material = "\n".join(
        [
            inspect.getsource(module.city_widths),
            inspect.getsource(module.features_of),
            repr(module.SIGNALS_FOR_FULL_STRENGTH),
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()


def test_baseline_version_constant_is_demo_1() -> None:
    from libs.domain.score_features import BASELINE_SOURCE_VERSION

    assert BASELINE_SOURCE_VERSION == "demo-1"
    assert "demo-1" in PINNED_ROWS


def test_seed_output_is_pinned_for_a_fixed_profile_set() -> None:
    from libs.domain.score_features import SCORE_SOURCE_VERSION

    rows = score_features(PINNED_PROFILES)
    assert rows == PINNED_ROWS[SCORE_SOURCE_VERSION]


def test_changed_formula_requires_a_new_version_string() -> None:
    from libs.domain.score_features import SCORE_SOURCE_VERSION

    assert SCORE_SOURCE_VERSION in FORMULA_FINGERPRINTS, "bump source_version and pin new rows"
    assert _formula_fingerprint() == FORMULA_FINGERPRINTS[SCORE_SOURCE_VERSION], (
        "formula changed: bump source_version as features-<major>.<minor> and pin its rows"
    )


# --- T-198: the signal.tagged id ---------------------------------------------------------------


def test_features_body_carries_the_tagged_event_id_or_none() -> None:
    a, b = uuid4(), uuid4()
    tagged = uuid4()
    page = [(a, CompanyProfile(city="Cali")), (b, CompanyProfile(city="Cali"))]
    body = build_features_body({"Cali": 2}, page, 10, {a: tagged})
    assert [c["parent_event_id"] for c in body["companies"]] == [str(tagged), None]
    assert build_features_body({"Cali": 2}, page, 10)["companies"][0]["parent_event_id"] is None


async def test_tagged_lookup_runs_after_the_context_and_filters_by_tenant() -> None:
    from services.core.app.score_features import TAGGED_SQL

    assert "tenant_id = :tenant_id" in TAGGED_SQL and "'signal.tagged'" in TAGGED_SQL
    engine = _Engine()
    await read_score_features(engine, TENANT_A, 10, None)  # type: ignore[arg-type]
    assert "set_config('app.tenant_id'" in engine.calls[0][0]
    tagged_calls = [c for c in engine.calls if "signal.tagged" in c[0]]
    assert len(tagged_calls) == 1 and tagged_calls[0][1]["tenant_id"] == TENANT_A  # type: ignore[index]
