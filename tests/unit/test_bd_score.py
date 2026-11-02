"""Tests for the score step of the Bright Data importer (``--phase score``).

Real companies are read from core's companies list (with ``signal_count``), checked against
ML for a stored score, scored with the seed heuristic through the seed's ``enrich``, and
briefed. Everything is faked: core and ML through ``httpx.MockTransport``, the scorer as an
in-memory port. No network and no Bright Data call.
"""

import argparse
import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from libs.common.internal_auth import verify_service_token
from libs.common.settings import Settings
from libs.domain.score_features import score_features
from ops import bd_import as bd
from ops.seed import enrich

TENANT = UUID("00000000-0000-4000-8000-0000000000a1")
SECRET = "s" * 32
REAL = "11111111-1111-4111-8111-111111111111"
REAL_NO_CITY = "22222222-2222-4222-8222-222222222222"
DEMO_SCORED = "33333333-3333-4333-8333-333333333333"
PAGES = {
    None: {
        "data": [
            {
                "id": REAL,
                "legal_name": "Global MVM",
                "city": "Medellin",
                "website": "https://www.globalmvm.com/",
                "employee_count": 337,
                "rues_status": "pending",
                "signal_count": 4,
            },
            {
                "id": REAL_NO_CITY,
                "legal_name": "Rappi",
                "city": None,
                "website": None,
                "employee_count": None,
                "rues_status": "pending",
                "signal_count": 9,
            },
        ],
        "next_cursor": "page-2",
    },
    "page-2": {
        "data": [
            {
                "id": DEMO_SCORED,
                "legal_name": "Demo",
                "city": "Medellin",
                "website": "https://demo.example",
                "rues_status": "matched",
                "signal_count": 2,
            }
        ],
        "next_cursor": None,
    },
}


class FakePort:
    def __init__(self) -> None:
        self.scored: list[dict[str, Any]] = []
        self.briefed: list[str] = []

    async def score(self, features: dict[str, Any]) -> dict[str, Any]:
        self.scored.append(features)
        return {"score": 50}

    async def brief(self, company_id: str) -> dict[str, Any]:
        self.briefed.append(company_id)
        return {}


def clients(seen: list[httpx.Request], pages: dict[Any, Any] = PAGES) -> tuple[Any, Any]:
    def core(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])

    def ml(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        stored = request.url.path.endswith(DEMO_SCORED)
        return httpx.Response(200, json={"score": 70}) if stored else httpx.Response(404)

    return (
        httpx.AsyncClient(transport=httpx.MockTransport(core), base_url="http://core.test"),
        httpx.AsyncClient(transport=httpx.MockTransport(ml), base_url="http://ml.test"),
    )


def make_run(seen: list[httpx.Request], *, rescore: bool = False, **kw: Any) -> tuple[Any, Any]:
    core_client, ml_client = clients(seen, **kw)
    port = FakePort()
    run = bd.Run(
        budget=bd.Budget(max_usd=1.0, caps=dict(bd.SOURCE_CAPS_USD)),
        ledger=bd.Ledger(None),
        landing=bd.NoLanding(),
        cities=["Medellin"],
        dry_run=False,
        companies=bd.TenantCompanies(core_client, ml_client, SECRET, TENANT),
        scorer=port,
        rescore=rescore,
    )
    return run, port


def test_profiles_come_from_core_pages_and_cope_without_nit_or_city() -> None:
    seen: list[httpx.Request] = []
    run, _port = make_run(seen)
    assert run.companies is not None
    profiles = asyncio.run(run.companies.profiles())
    assert set(profiles) == {REAL, REAL_NO_CITY, DEMO_SCORED}
    assert profiles[REAL] == enrich.CompanyProfile("Medellin", True, False, 4)
    assert profiles[REAL_NO_CITY] == enrich.CompanyProfile(None, False, False, 9)
    assert profiles[DEMO_SCORED].has_nit
    first = seen[0]
    assert first.url.path == "/internal/core/companies" and first.url.params["limit"] == "200"
    assert first.headers["X-Tenant-Id"] == str(TENANT)
    assert verify_service_token(first.headers["X-Service-Token"], SECRET) == "seed"
    features = {row["company_id"]: row for row in score_features(profiles)}
    for row in features.values():
        assert all(
            0 <= row[k] <= 1 for k in ("icp_fit", "signal_strength", "territory_opportunity")
        )
    assert features[REAL]["icp_fit"] == pytest.approx(0.7)  # website, no NIT
    assert features[REAL_NO_CITY]["icp_fit"] == pytest.approx(0.4)


def test_score_step_scores_only_unscored_companies_with_tenant_wide_features() -> None:
    seen: list[httpx.Request] = []
    run, port = make_run(seen)
    asyncio.run(run.phase_score())
    assert sorted(f["company_id"] for f in port.scored) == sorted([REAL, REAL_NO_CITY])
    assert sorted(port.briefed) == sorted([REAL, REAL_NO_CITY])
    every = {
        row["company_id"]: row for row in score_features(asyncio.run(run.companies.profiles()))
    }
    for features in port.scored:
        assert features == every[features["company_id"]]  # territory over all three companies
    stats = run.stats["score"]
    assert stats.records == 3 and stats.skipped == 1 and stats.calls == 2 and not stats.failures
    assert stats.notes == ["scored 2, briefed 2, failed 0"]


def test_rescore_scores_every_company() -> None:
    run, port = make_run([], rescore=True)
    asyncio.run(run.phase_score())
    assert len(port.scored) == 3 and run.stats["score"].skipped == 0


def test_core_without_signal_count_stops_the_step() -> None:
    old = {None: {"data": [{"id": REAL, "city": "Cali"}], "next_cursor": None}}
    run, port = make_run([], pages=old)
    asyncio.run(run.phase_score())
    assert port.scored == []
    assert "rebuild it" in run.stats["score"].failures[0]


def test_enrich_only_keeps_the_seed_default() -> None:
    profiles = {REAL: enrich.CompanyProfile("Cali", True, False, 3), "b": enrich.CompanyProfile()}
    port = FakePort()
    asyncio.run(enrich.enrich(port, profiles, sleep=asyncio.sleep))
    assert len(port.scored) == 2
    limited = FakePort()
    asyncio.run(enrich.enrich(limited, profiles, sleep=asyncio.sleep, only={"b"}))
    assert [f["company_id"] for f in limited.scored] == ["b"]


def test_score_phase_needs_no_bright_data_token(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        phase="score",
        cities=None,
        dry_run=True,
        max_usd=None,
        timeout=10,
        state_dir=None,
        fixtures=Path("unused"),
        rescore=False,
    )
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert asyncio.run(bd.execute(args, settings)) == 0
    assert "plan score" in capsys.readouterr().out
    assert bd.PHASE_PLAN["all"] == ("1", "2", "score", "3")
