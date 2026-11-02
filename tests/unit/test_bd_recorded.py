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

import pytest

from ops import bd_import as bd
from services.ingest.app.mapping import load_mapping, normalize_record

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
        assert fields["company_id"] and fields["company_url"].startswith("https://")
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
    assert text.count("@") == text.count("redacted@example.invalid")
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
