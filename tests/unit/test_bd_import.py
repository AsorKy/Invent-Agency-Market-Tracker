"""Unit tests for the Bright Data importer (``ops/bd_import.py``).

Every provider is faked: REST calls go through ``httpx.MockTransport``, the SDK client
is a stub with the attributes the adapter reads, and landing, core, and the ledger are
in memory. No network and no database are used.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from libs.common.settings import Settings
from ops import bd_import as bd
from services.ingest.app.connectors import BrightDataConnector

TENANT = UUID("00000000-0000-4000-8000-0000000000a1")
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


# ---------------------------------------------------------------- budget and keys


def test_price_table_bills_records_and_search_blocks() -> None:
    assert bd.PRICES["maps"].cost(40) == pytest.approx(0.08)
    assert bd.PRICES["business_search_people"].cost(10) == pytest.approx(0.002)
    assert bd.PRICES["business_search_companies"].cost(101) == pytest.approx(0.004)
    assert bd.PRICES["linkedin_jobs"].cost(0) == 0.0
    assert set(bd.PRICES) == set(bd.SOURCES) == set(bd.SOURCE_CAPS_USD)


def test_budget_blocks_before_the_run_or_source_cap() -> None:
    budget = bd.Budget(max_usd=0.1, caps={"maps": 0.05, "linkedin_jobs": 1.0})
    budget.reserve("maps", 25)
    with pytest.raises(bd.BudgetExceededError, match="source cap"):
        budget.reserve("maps", 1)
    with pytest.raises(bd.BudgetExceededError, match="run cap"):
        budget.reserve("linkedin_jobs", 26)
    assert budget.total == pytest.approx(0.05)
    budget.settle("maps", 0.05, 30)  # the provider delivered more than requested
    assert budget.total == pytest.approx(0.06)


def test_budget_reads_environment_and_caps_the_probe() -> None:
    env = {"BD_IMPORT_MAX_USD": "7", "BD_IMPORT_MAX_USD_MAPS": "0.5"}
    full = bd.budget_from_env(env, max_usd=None, phase="1")
    assert full.max_usd == 7.0 and full.caps["maps"] == 0.5
    assert bd.budget_from_env({}, max_usd=None, phase="all").max_usd == bd.DEFAULT_MAX_USD
    assert bd.budget_from_env(env, max_usd=None, phase="0").max_usd == bd.PROBE_MAX_USD


def test_call_key_is_stable_and_ledger_persists(tmp_path: Path) -> None:
    key = bd.call_key("maps", {"b": 1, "a": [1, 2]})
    assert key == bd.call_key("maps", {"a": [1, 2], "b": 1}) != bd.call_key("x", {"b": 1})
    ledger = bd.Ledger(tmp_path / "state" / "ledger.json")
    ledger.record(key, {"records": 3})
    assert bd.Ledger(tmp_path / "state" / "ledger.json").done(key)
    assert not bd.Ledger(None).done(key)


# ---------------------------------------------------------------- request builders


def test_request_builders_follow_the_spec() -> None:
    inputs = bd.maps_inputs("Cali", ["hotel"])
    assert inputs == [
        {"country": "CO", "lat": 3.4516, "long": -76.532, "zoom_level": 13, "keyword": "hotel"}
    ]
    assert bd.maps_params(40) == {
        "type": "discover_new",
        "discover_by": "location",
        "limit_per_input": "40",
    }
    assert bd.jobs_inputs("Bogota", ["desarrollador"])[0]["country"] == "CO"
    company = bd.company_search_body("Medellin", "software", 100)
    conditions = company["query"]["and"]
    assert company["mode"] == "ludicrous" and company["limit"] == 100
    assert {"range": {"employees_in_linkedin": {">=": 11, "<=": 500}}} in conditions
    people = bd.people_search_body("Acme SAS", 10)
    text = json.dumps(people)
    assert "Gerente General" in text and "Acme SAS" in text and '"country_code": "CO"' in text
    assert people["view"] == bd.PEOPLE_VIEW


def test_parse_cities_accepts_any_spelling() -> None:
    assert bd.parse_cities(None) == list(bd.CANONICAL_CITIES)
    assert bd.parse_cities("medell\u00edn, BOGOTA,Medellin") == ["Medellin", "Bogota"]
    with pytest.raises(SystemExit):
        bd.parse_cities("Lima")


def test_routing_problems_name_the_missing_env_lines() -> None:
    configured = settings(
        source_maps_dataset_id=bd.MAPS_DATASET_ID,
        source_linkedin_jobs_enabled=True,
        source_linkedin_jobs_dataset_id="other",
    )
    problems = bd.routing_problems(configured, ["maps", "linkedin_jobs", "business_search_people"])
    assert problems == [
        f"SOURCE_LINKEDIN_JOBS_ENABLED=true and SOURCE_LINKEDIN_JOBS_DATASET_ID="
        f"{bd.LINKEDIN_JOBS_DATASET_ID}"
    ]


# ---------------------------------------------------------------- personal data


def test_person_keys_are_dropped_before_landing() -> None:
    record = {"job_title": "Dev", "job_poster": {"name": "A"}, "nested": [{"Employees": [1]}]}
    assert bd.drop_person_keys(record) == {"job_title": "Dev", "nested": [{}]}


def test_fixture_redaction_keeps_keys_and_types() -> None:
    person = {"bright_id": "x", "data": {"name": "Ana", "url": "https://l.in/x", "n": 3, "f": 1.5}}
    assert bd.redact_fixture(person, person=True) == {
        "bright_id": "redacted",
        "data": {"name": "redacted", "url": "https://example.invalid/redacted", "n": 0, "f": 0.0},
    }
    company = {"name": "Acme", "employees": [{"title": "Ana"}], "reviews": [{"user": "B"}]}
    redacted = bd.redact_fixture(company, person=False)
    assert redacted["name"] == "Acme"
    assert redacted["employees"] == [{"title": "redacted"}]
    assert redacted["reviews"] == [{"user": "redacted"}]


def test_decision_makers_keep_only_name_title_and_public_profile() -> None:
    documents = [
        {
            "bright_id": "p1",
            "data": {
                "name": " Ana Ruiz ",
                "current_title": "Gerente General",
                "url": "https://www.linkedin.com/in/ana?trk=x",
                "email": "ana@example.com",
                "phone": "+57 300",
            },
        },
        {"bright_id": "p2", "data": {"name": "Bo", "current_title": "Analista"}},
        {"bright_id": "p3", "data": {"name": "Cy", "current_title": "Co-Founder & CTO"}},
        {"bright_id": "p3", "data": {"name": "Cy", "current_title": "CTO"}},
        "not a document",
    ]
    people = bd.decision_makers(documents)
    assert people == [
        {
            "name": "Ana Ruiz",
            "title": "Gerente General",
            "profile_url": "https://www.linkedin.com/in/ana",
        },
        {"name": "Cy", "title": "Co-Founder & CTO", "public_profile_id": "p3"},
    ]
    body, key = bd.decision_maker_signal("c-1", people[0], TENANT, NOW)
    assert body["signal_type"] == "decision_maker" and body["source"] == "business_search_people"
    assert set(body["payload"]) == {"name", "title", "profile_url", "source", "found_at"}
    assert "email" not in json.dumps(body) and "+57" not in json.dumps(body)
    assert body["source_external_id"] == f"dm-{key[:40]}"
    assert bd.decision_maker_signal("c-1", people[0], TENANT, NOW)[1] == key


def test_field_coverage_counts_non_empty_leaves() -> None:
    coverage = bd.field_coverage([{"a": 1, "b": {"c": ""}}, {"a": None, "b": {"c": "x"}}])
    assert coverage == {"a": 1, "b.c": 1}


# ---------------------------------------------------------------- connector


def test_connector_sends_extra_trigger_params_and_posts_json() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/search/company":
            return httpx.Response(200, json={"documents": []})
        return httpx.Response(200, json={"snapshot_id": "s_1"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://bd.test")
    connector = BrightDataConnector(
        "maps", client, dataset_id="gd_x", api_token="t", extra_params=bd.maps_params(3)
    )
    assert connector.trigger([{"keyword": "hotel"}]).snapshot_id == "s_1"
    params = seen[0].url.params
    assert params["type"] == "discover_new" and params["discover_by"] == "location"
    assert params["limit_per_input"] == "3" and params["dataset_id"] == "gd_x"
    assert connector.post_json("/search/company", {"q": 1}) == {"documents": []}
    assert json.loads(seen[1].content) == {"q": 1}


# ---------------------------------------------------------------- fakes for a run


class FakeLanding:
    def __init__(self, landed: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.landed = landed or {}
        self.calls: list[tuple[str, str, int]] = []

    async def land(self, source: str, provider_id: str, records: list[dict[str, Any]]) -> None:
        self.calls.append((source, provider_id, len(records)))
        self.landed.setdefault(source, []).extend(records)

    async def landed_records(self, source: str) -> list[dict[str, Any]]:
        return list(self.landed.get(source, []))


class FakeCore:
    def __init__(self, companies: list[bd.ScoredCompany]) -> None:
        self.companies = companies
        self.signals: list[tuple[dict[str, Any], str]] = []

    async def scored_companies(self) -> list[bd.ScoredCompany]:
        return self.companies

    async def create_signal(self, body: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        self.signals.append((body, idempotency_key))
        return {"status": "created"}


def provider(
    requests: list[httpx.Request], forbidden: frozenset[str] = frozenset()
) -> httpx.MockTransport:
    """Datasets API plus Business Search, answering like Bright Data does."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path in forbidden:
            return httpx.Response(403, json={"error": "not on the allowlist"})
        if path == "/datasets/v3/trigger":
            return httpx.Response(200, json={"snapshot_id": f"s_{len(requests)}"})
        if path.startswith("/datasets/v3/progress/"):
            return httpx.Response(200, json={"status": "ready", "records": 2})
        if path.startswith("/datasets/v3/snapshot/"):
            return httpx.Response(
                200,
                json=[
                    {"place_id": "p1", "name": "Hotel Uno", "phone_number": "+57 4 1"},
                    {"error": "dead page", "error_code": "dead_page"},
                ],
            )
        if path == "/search/company":
            company = {"name": "Acme SAS", "employees_in_linkedin": 40, "website": "https://a.co"}
            return httpx.Response(200, json={"documents": [{"bright_id": "b1", "data": company}]})
        if path == "/search/people":
            person = {"name": "Ana", "current_title": "CEO", "url": "https://www.linkedin.com/in/a"}
            return httpx.Response(200, json={"documents": [{"bright_id": "h1", "data": person}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


EMPLOYEES = [
    {
        "title": "Ana Ruiz",
        "subtitle": "CEO en Acme",
        "link": "https://co.linkedin.com/in/ana-ruiz?trk=x",
        "img": "https://media.example/ana.jpg",
    },
    {"title": "Bo Diaz", "subtitle": "Asistente del CEO", "link": "https://www.linkedin.com/in/bo"},
    {"title": "Cy Paz", "subtitle": "Ingeniera de datos", "link": "https://www.linkedin.com/in/cy"},
    {"title": "Dee Ro", "subtitle": "Directora Comercial"},
    {"title": "Eli Mar", "link": "https://www.linkedin.com/in/eli"},
]
ANA = {
    "name": "Ana Ruiz",
    "title": "CEO en Acme",
    "profile_url": "https://www.linkedin.com/in/ana-ruiz",
}


def sdk_client(calls: list[str]) -> Any:
    async def execute(**kwargs: Any) -> Any:
        calls.append(f"jobs:{kwargs['limit_per_input']}:{kwargs['extra_params']['discover_by']}")
        jobs = [
            {
                "job_posting_id": "j1",
                "job_title": "Dev",
                "company_name": "Acme SAS",
                "company_url": "https://www.linkedin.com/company/acme?trk=1",
                "job_poster": {"name": "Recruiter"},
            }
        ]
        return SimpleNamespace(success=True, data=jobs, snapshot_id="sj", status="ready")

    async def companies(url: list[str], timeout: int) -> Any:
        calls.append(f"companies:{len(url)}")
        return [
            SimpleNamespace(
                success=True,
                data={"name": "Acme SAS", "url": u, "employees": EMPLOYEES},
                snapshot_id="sc",
                status="ready",
            )
            for u in url
        ]

    search = SimpleNamespace(
        DATASET_ID_JOBS=bd.LINKEDIN_JOBS_DATASET_ID,
        workflow_executor=SimpleNamespace(execute=execute),
    )
    scrape = SimpleNamespace(
        DATASET_ID_COMPANIES=bd.LINKEDIN_COMPANIES_DATASET_ID, companies=companies
    )
    return SimpleNamespace(
        search=SimpleNamespace(linkedin=search), scrape=SimpleNamespace(linkedin=scrape)
    )


async def no_sleep(_seconds: float) -> None:
    return None


def make_run(
    tmp_path: Path,
    *,
    landing: FakeLanding | None = None,
    core: FakeCore | None = None,
    max_usd: float = 25.0,
    dry_run: bool = False,
    forbidden: frozenset[str] = frozenset(),
) -> tuple[bd.Run, list[httpx.Request], list[str]]:
    requests: list[httpx.Request] = []
    sdk_calls: list[str] = []
    http = httpx.Client(transport=provider(requests, forbidden), base_url="https://bd.test")
    rest = bd.BrightDataRest(http, "token", settings(), sleep=no_sleep)
    run = bd.Run(
        budget=bd.Budget(max_usd=max_usd, caps=dict(bd.SOURCE_CAPS_USD)),
        ledger=bd.Ledger(tmp_path / "ledger.json"),
        landing=landing or FakeLanding(),
        cities=["Medellin"],
        dry_run=dry_run,
        rest=rest,
        sdk=bd.LinkedInSdk(sdk_client(sdk_calls), rest),
        core=core,
        tenant_id=TENANT,
        clock=lambda: NOW,
    )
    return run, requests, sdk_calls


def test_phase1_lands_every_source_and_a_rerun_skips_paid_calls(tmp_path: Path) -> None:
    landing = FakeLanding()
    run, requests, sdk_calls = make_run(tmp_path, landing=landing)
    asyncio.run(run.phase1())
    sources = sorted({call[0] for call in landing.calls})
    assert sources == ["business_search_companies", "linkedin_jobs", "maps"]
    trigger = next(r for r in requests if r.url.path == "/datasets/v3/trigger")
    assert trigger.url.params["dataset_id"] == bd.MAPS_DATASET_ID
    assert trigger.url.params["limit_per_input"] == str(bd.MAPS_LIMIT)
    assert len(json.loads(trigger.content)) == len(bd.MAPS_KEYWORDS)
    assert sdk_calls == [f"jobs:{bd.JOBS_LIMIT}:keyword"]
    assert all("job_poster" not in r for r in landing.landed["linkedin_jobs"])
    assert run.stats["maps"].errors == 1 and run.stats["maps"].records == 1
    assert run.stats["maps"].with_contact == 1
    assert run.stats["business_search_companies"].mid_size == 2
    expected = 6 * 40 * 0.002 + 3 * 50 * 0.002 + 2 * 0.002
    assert run.budget.total == pytest.approx(expected)

    again, requests_again, sdk_again = make_run(tmp_path, landing=landing)
    asyncio.run(again.phase1())
    assert requests_again == [] and sdk_again == []
    assert again.stats["maps"].skipped == 1 and again.budget.total == 0.0


def test_budget_stop_is_reported_and_nothing_is_called(tmp_path: Path) -> None:
    run, requests, sdk_calls = make_run(tmp_path, max_usd=0.01)
    asyncio.run(run.phase1())
    assert not any(r.url.path == "/datasets/v3/trigger" for r in requests) and sdk_calls == []
    assert "BudgetExceededError" in run.stats["maps"].failures[0]
    assert run.stats["business_search_companies"].calls == 2


def test_dry_run_plans_without_calls(tmp_path: Path) -> None:
    run, requests, sdk_calls = make_run(tmp_path, dry_run=True)
    run.ledger.path = None
    for phase in (run.phase1, run.phase2, run.phase3):
        asyncio.run(phase())
    assert requests == [] and sdk_calls == []
    assert run.stats["business_search_people"].calls == bd.TOP_COMPANIES
    # One city: maps 240 + jobs 150 records, 2 company searches, 300 URLs, 150 people searches.
    assert run.budget.total == pytest.approx((240 + 150 + 300) * 0.002 + (2 + 150) * 0.002)


def test_phase2_scrapes_new_company_urls_once(tmp_path: Path) -> None:
    landing = FakeLanding(
        {
            "linkedin_jobs": [
                {"company_url": "https://www.linkedin.com/company/acme/?trk=1"},
                {"company_url": "https://www.linkedin.com/company/acme"},
                {"company_url": "https://www.linkedin.com/company/done"},
                {"company_url": "https://example.com/not-linkedin"},
            ],
            "linkedin_companies": [{"url": "https://www.linkedin.com/company/done"}],
        }
    )
    run, _requests, sdk_calls = make_run(tmp_path, landing=landing)
    asyncio.run(run.phase2())
    assert sdk_calls == ["companies:1"]
    landed = landing.landed["linkedin_companies"][-1]
    assert landed["url"] == "https://www.linkedin.com/company/acme" and "employees" not in landed
    # Only the decision maker survives, with name, title, and public profile (INV-12).
    assert landed["decision_makers"] == [ANA]
    text = json.dumps(landing.landed)
    assert all(other not in text for other in ("Bo Diaz", "Cy Paz", "Dee Ro", "Eli Mar", "img"))


def test_phase3_creates_decision_maker_signals_for_imported_companies(tmp_path: Path) -> None:
    landing = FakeLanding(
        {"business_search_companies": [{"bright_id": "b1", "data": {"name": "Acme SAS"}}]}
    )
    core = FakeCore(
        [
            bd.ScoredCompany("c-1", "ACME SAS", 90),
            bd.ScoredCompany("c-2", "Demo Company", 99),
        ]
    )
    run, requests, _sdk = make_run(tmp_path, landing=landing, core=core)
    asyncio.run(run.phase3())
    searches = [r for r in requests if r.url.path == "/search/people"]
    assert len(searches) == 1 and "ACME SAS" in searches[0].content.decode()
    assert len(core.signals) == 1
    body, key = core.signals[0]
    assert body["company_id"] == "c-1" and body["tenant_id"] == str(TENANT) and len(key) == 64
    assert body["payload"]["profile_url"] == "https://www.linkedin.com/in/a"
    stats = run.stats["business_search_people"]
    assert stats.companies_searched == 1 and stats.companies_with_dm == 1
    assert not landing.calls  # people never enter the company mapping path

    again, requests_again, _ = make_run(tmp_path, landing=landing, core=core)
    asyncio.run(again.phase3())
    assert requests_again == [] and len(core.signals) == 1


def test_sdk_timeout_falls_back_to_the_datasets_api(tmp_path: Path) -> None:
    run, requests, _sdk = make_run(tmp_path)
    timed_out = SimpleNamespace(success=False, data=None, snapshot_id="s9", status="timeout")
    result = asyncio.run(
        run._sdk()._finish(timed_out, "linkedin_jobs", bd.LINKEDIN_JOBS_DATASET_ID)
    )
    assert result.provider_id == "s9" and len(result.records) == 2
    assert any(r.url.path == "/datasets/v3/snapshot/s9" for r in requests)
    failed = SimpleNamespace(success=False, data=None, snapshot_id=None, status="error", error="x")
    with pytest.raises(bd.ImportCallError):
        asyncio.run(run._sdk()._finish(failed, "linkedin_jobs", bd.LINKEDIN_JOBS_DATASET_ID))


def test_sdk_dataset_ids_are_checked() -> None:
    client = sdk_client([])
    client.search.linkedin.DATASET_ID_JOBS = "changed"
    with pytest.raises(bd.ImportCallError, match="dataset ids changed"):
        bd.LinkedInSdk(client, None)  # type: ignore[arg-type]


def test_main_dry_run_needs_no_token_or_network(capsys: pytest.CaptureFixture[str]) -> None:
    code = asyncio.run(
        bd.execute(
            argparse.Namespace(
                phase="1",
                cities="Cali",
                dry_run=True,
                max_usd=None,
                timeout=10,
                state_dir=None,
                fixtures=Path("unused"),
            ),
            settings(),
        )
    )
    out = capsys.readouterr().out
    assert code == 0 and "plan maps" in out and "estimated total" in out


def test_live_run_refuses_without_token_or_routing(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        phase="1",
        cities=None,
        dry_run=False,
        max_usd=None,
        timeout=10,
        state_dir=None,
        fixtures=Path("unused"),
    )
    assert asyncio.run(bd.execute(args, settings())) == 2
    assert "BRIGHT_DATA_API_TOKEN is not set" in capsys.readouterr().err
    assert asyncio.run(bd.execute(args, settings(bright_data_api_token="t" * 20))) == 2
    err = capsys.readouterr().err
    assert f"SOURCE_MAPS_DATASET_ID={bd.MAPS_DATASET_ID}" in err and "t" * 20 not in err


def test_http_core_pages_leads_and_writes_keyed_signals() -> None:
    seen: list[httpx.Request] = []
    pages = {
        None: {
            "data": [
                {"company_id": "c1", "legal_name": "Acme", "score": 40},
                {"company_id": "c2", "legal_name": "Beta", "score": None},
            ],
            "next_cursor": "n1",
        },
        "n1": {"data": [{"company_id": "c1", "legal_name": "Acme", "score": 70}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=pages[request.url.params.get("cursor")])
        return httpx.Response(201, json={"status": "created"})

    async def go() -> list[bd.ScoredCompany]:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://core.test"
        )
        core = bd.HttpCore(settings(internal_service_token="s" * 32), TENANT, client)
        companies = await core.scored_companies()
        await core.create_signal({"company_id": "c1"}, idempotency_key="k" * 64)
        await client.aclose()
        return companies

    assert asyncio.run(go()) == [bd.ScoredCompany("c1", "Acme", 70)]
    read, write = seen[0], seen[-1]
    assert read.url.path == "/internal/core/leads" and read.headers["X-Tenant-Id"] == str(TENANT)
    assert read.headers["X-Service-Token"].startswith("v1.verify_live.")
    assert write.url.path == "/internal/core/signals"
    assert write.headers["Idempotency-Key"] == "k" * 64
    assert write.headers["X-Service-Token"].startswith("v1.ingest.")


def test_employee_titles_cover_spanish_and_english_variants() -> None:
    for title in (
        "CEO",
        "Chief Executive Officer",
        "Gerente General",
        "General Manager",
        "Co-Founder & CTO",
        "Cofundadora",
        "Fundador y Director",
        "Directora Comercial",
        "Sales Director",
        "Directora de Mercadeo",
        "Marketing Director",
        "Chief Technology Officer",
    ):
        assert bd.is_decision_title(title), title
    for title in ("Asistente del CEO", "Former CEO", "Ingeniera", "Director de Proyectos", ""):
        assert not bd.is_decision_title(title), title


def test_phase3_takes_decision_makers_from_profiles_first(tmp_path: Path) -> None:
    profile = {
        "company_id": "7",
        "name": "Acme SAS",
        "url": "https://www.linkedin.com/company/acme",
        "decision_makers": [
            ANA,
            {"name": "Xi", "title": "Analyst", "profile_url": "https://www.linkedin.com/in/xi"},
            {"name": "Yu", "title": "CTO"},
        ],
    }
    landing = FakeLanding({"linkedin_companies": [profile]})
    core = FakeCore([bd.ScoredCompany("c-1", "Acme SAS", 80)])
    run, requests, _sdk = make_run(tmp_path, landing=landing, core=core)
    asyncio.run(run.phase3())
    assert not any(r.url.path == "/search/people" for r in requests)
    assert len(core.signals) == 1
    body, _key = core.signals[0]
    assert body["source"] == "linkedin_companies" and body["signal_type"] == "decision_maker"
    assert set(body["payload"]) == {"name", "title", "profile_url", "source", "found_at"}
    summary = run.stats["decision_makers"]
    assert summary.companies_searched == summary.companies_with_dm == 1


def test_business_search_403_is_skipped_cleanly_and_not_billed(tmp_path: Path) -> None:
    forbidden = frozenset({"/search/company", "/search/people"})
    core = FakeCore([bd.ScoredCompany("c-1", "Acme SAS", 80), bd.ScoredCompany("c-2", "Beta", 70)])
    landing = FakeLanding(
        {
            "maps": [{"place_id": "p", "name": "Acme SAS"}],
            "linkedin_jobs": [{"job_posting_id": "j", "job_title": "D", "company_name": "Beta"}],
        }
    )
    run, requests, _sdk = make_run(tmp_path, landing=landing, core=core, forbidden=forbidden)
    asyncio.run(run.phase1())
    asyncio.run(run.phase3())
    company, people = run.stats["business_search_companies"], run.stats["business_search_people"]
    assert (
        company.failures == []
        and people.failures == []
        and run.stats["decision_makers"].failures == []
    )
    assert "HTTP 401/403" in company.notes[0] and "HTTP 401/403" in people.notes[0]
    assert run.budget.spent["business_search_companies"] == 0.0
    assert run.budget.spent["business_search_people"] == 0.0
    # Searches already in flight may also get the 403; after it, none is sent.
    assert 1 <= sum(r.url.path == "/search/people" for r in requests) <= bd.CONCURRENCY
    summary = run.stats["decision_makers"]
    assert summary.companies_searched == 2 and summary.companies_with_dm == 0
    assert core.signals == []
