"""Tests on the Bright Data samples recorded by ``make bd-import PHASE=0`` (decision D-13).

The samples under ``tests/fixtures/brightdata/recorded/`` are real provider records,
redacted by ``ops/bd_import.py``: person values are placeholders and no request header is
stored. These tests prove the verified mappings on them and that the importer lands them
without person data. No network is used.
"""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from libs.domain.entity_resolution import Resolution
from ops import bd_import as bd
from services.core.app.companies import CompanyUpsert, company_slug
from services.ingest.app.mapping import load_mapping, normalize_record
from services.ingest.app.to_core import Registered, SnapshotFlow

RECORDED = Path(__file__).resolve().parents[1] / "fixtures" / "brightdata" / "recorded"


def recorded(source: str) -> list[dict[str, Any]]:
    path = RECORDED / f"{source}.json"
    if not path.is_file():
        pytest.skip(f"no recorded sample for {source}")
    records = json.loads(path.read_text(encoding="utf-8"))
    return [r for r in records if not bd.is_error_record(r)]


def test_linkedin_jobs_mapping_reads_the_real_fields() -> None:
    records = recorded("linkedin_jobs")
    mapping = load_mapping("linkedin_jobs")
    assert not mapping.unverified and records
    for record in records:
        fields = normalize_record(mapping, record).fields
        assert fields["title"] and fields["company_name"]
        assert fields["city"] == "Medellin"
        assert fields["keyword"] == "desarrollador"
        assert fields["linkedin_company_id"] and fields["company_url"].startswith("https://")
        assert bd.company_url(fields["company_url"]).startswith("https://www.linkedin.com/company/")


def test_linkedin_companies_mapping_keeps_size_industry_and_related_companies() -> None:
    records = recorded("linkedin_companies")
    mapping = load_mapping("linkedin_companies")
    assert not mapping.unverified and records
    for record in records:
        fields = normalize_record(mapping, record).fields
        assert isinstance(fields["employee_count"], int)
        assert fields["industry"] and fields["website"] and fields["follower_count"]
        assert isinstance(fields["similar"], list) and fields["similar"][0]["title"]
        assert fields["city"] in (None, *bd.CANONICAL_CITIES)


def test_jobs_and_profiles_link_by_company_id() -> None:
    jobs = {r["company_id"] for r in recorded("linkedin_jobs")}
    companies = {r["company_id"] for r in recorded("linkedin_companies")}
    assert jobs & companies


@pytest.mark.parametrize(
    "name", ["linkedin_jobs.json", "linkedin_companies.json", "errors/maps.crawl_error.json"]
)
def test_recorded_samples_carry_no_person_data(name: str) -> None:
    path = RECORDED / name
    if not path.is_file():
        pytest.skip(f"no recorded sample {name}")
    text = path.read_text(encoding="utf-8")
    assert "authorization" not in text.lower() and "bearer" not in text.lower()
    assert "@" not in text  # e-mail addresses are masked
    for record in json.loads(text):
        for key in bd.PERSON_KEYS & set(record):
            assert record[key] in (None, []) or "redacted" in json.dumps(record[key])


def test_landing_drops_person_lists_of_real_records() -> None:
    companies = recorded("linkedin_companies")
    landed = [bd.drop_person_keys(r) for r in companies]
    assert all("employees" not in r and "updates" not in r for r in landed)
    assert all(r["similar"] == c["similar"] for r, c in zip(landed, companies, strict=True))


def test_stats_on_real_records() -> None:
    stats = bd.SourceStats()
    stats.add_records("linkedin_companies", recorded("linkedin_companies"))
    assert stats.records == stats.with_contact == stats.with_size
    assert 0 <= stats.mid_size <= stats.with_size


def test_phase2_reads_company_urls_from_recorded_jobs(tmp_path: Path) -> None:
    class Landed:
        async def land(self, *_args: Any) -> None:
            return None

        async def landed_records(self, source: str) -> list[dict[str, Any]]:
            return recorded("linkedin_jobs") if source == "linkedin_jobs" else []

    run = bd.Run(
        budget=bd.Budget(max_usd=1.0, caps=dict(bd.SOURCE_CAPS_USD)),
        ledger=bd.Ledger(None),
        landing=Landed(),
        cities=["Medellin"],
        dry_run=False,
    )
    urls = asyncio.run(run.company_urls())
    assert urls and all(u.startswith("https://www.linkedin.com/company/") for u in urls)
    assert all("?" not in u for u in urls) and len(urls) == len(set(urls))


def test_maps_probe_error_is_kept_outside_the_samples() -> None:
    errors = json.loads((RECORDED / "errors" / "maps.crawl_error.json").read_text("utf-8"))
    assert all(bd.is_error_record(r) and r["error_code"] == "crawl_error" for r in errors)
    assert not (RECORDED / "maps.json").exists() and load_mapping("maps").unverified


# ---------------------------------------------------------------- one core company per LinkedIn id


class SlugCore:
    """Core double that keys companies by core's own ``company_slug`` (as the upsert does)."""

    def __init__(self) -> None:
        self.companies: dict[str, str] = {}
        self.signals: list[dict[str, Any]] = []

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]:
        slug = company_slug(CompanyUpsert(**body))
        company_id = self.companies.setdefault(slug, str(uuid4()))
        return {"company_id": company_id, "status": "created", "version": 1}

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]:
        self.signals.append(body)
        return {"status": "created"}


class NoFailures:
    async def record(self, **kwargs: Any) -> None:
        raise AssertionError(f"unexpected failure: {kwargs['reason']}")


async def _deliver_all(records: dict[str, list[dict[str, Any]]]) -> SlugCore:
    core = SlugCore()
    flow = SnapshotFlow(
        core=core,
        failures=NoFailures(),
        resolve=lambda candidate: Resolution(decision="new_company"),
        dataset_sources={},
        tenant_id=UUID("00000000-0000-4000-8000-0000000000a1"),
        retry_attempts=1,
    )

    async def register(name: str, city: str | None, nit: str | None) -> Registered:
        return Registered(id=str(uuid4()))

    for source, items in records.items():
        for record in items:
            outcome = await flow.process_record(
                source=source,
                snapshot_id="s",
                record=bd.prepare_record(source, record),
                register=register,
            )
            assert outcome.status == "delivered"
    return core


def test_jobs_and_their_profiles_resolve_to_one_core_company_each() -> None:
    jobs, companies = recorded("linkedin_jobs"), recorded("linkedin_companies")
    assert len(jobs) == len(companies) == 3
    core = asyncio.run(_deliver_all({"linkedin_jobs": jobs, "linkedin_companies": companies}))
    assert len(core.companies) == 3  # not 6
    assert len(core.signals) == 6


def test_without_the_linkedin_id_the_same_records_split_into_six_companies() -> None:
    def strip(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in r.items() if k != "company_id"} | {"id": r["url"]} for r in records
        ]

    jobs = [{k: v for k, v in r.items() if k != "company_id"} for r in recorded("linkedin_jobs")]
    core = asyncio.run(
        _deliver_all(
            {"linkedin_jobs": jobs, "linkedin_companies": strip(recorded("linkedin_companies"))}
        )
    )
    assert len(core.companies) == 6


# ---------------------------------------------------------------- e-mail masking (INV-12)


def test_emails_in_job_text_are_masked_before_mapping_and_core() -> None:
    record = dict(recorded("linkedin_jobs")[0])
    record["job_summary"] = "Send your CV to ana.ruiz@acme.com.co or call. Talento@ACME.co too."
    record["discovery_input"] = {"keyword": "desarrollador", "note": "x@y.io"}
    prepared = bd.prepare_record("linkedin_jobs", record)
    assert "@" not in json.dumps(prepared)
    assert prepared["job_summary"].count(bd.EMAIL_MASK) == 2
    fields = normalize_record(load_mapping("linkedin_jobs"), prepared).fields
    assert "@" not in json.dumps(fields)
    core = asyncio.run(_deliver_all({"linkedin_jobs": [record]}))
    assert "@" not in json.dumps(core.signals)


def test_recorded_profiles_yield_no_decision_maker_without_a_role_field() -> None:
    # Phase 0: employees entries carry img, link, and title (the person's name) only.
    for record in recorded("linkedin_companies"):
        prepared = bd.prepare_record("linkedin_companies", record)
        assert prepared["decision_makers"] == [] and "employees" not in prepared
