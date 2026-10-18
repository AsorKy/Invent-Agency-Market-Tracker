"""Unit tests for the LinkedIn and Instagram source mappings (REQ-ING-004, T-048).

Checks that each mapping loads, is flagged ``unverified`` while no recorded
provider sample exists (D-13), normalizes a documented-shape record, and that
``build_connector`` builds mock and live connectors for these sources. No
network is used.
"""

import httpx
import pytest

from libs.common.settings import Settings
from services.ingest.app.connectors import BrightDataConnector, MockConnector
from services.ingest.app.mapping import MAPPINGS_DIR, MappingError, load_mapping, normalize_record
from services.ingest.app.source_config import build_connector

SOURCES = ["linkedin_companies", "linkedin_jobs", "instagram"]

RECORDS = {
    "linkedin_companies": {
        "company_id": "acme-sas",
        "name": " Acme SAS ",
        "followers": "1,200",
        "employees_in_linkedin": 45,
        "website": "https://acme.example",
        "url": "https://www.linkedin.com/company/acme-sas",
    },
    "linkedin_jobs": {
        "job_posting_id": "123",
        "job_title": "Analyst",
        "company_name": "Acme SAS",
        "job_location": "Medellin, Antioquia",
        "url": "https://www.linkedin.com/jobs/view/123",
    },
    "instagram": {
        "id": "999",
        "account": "acme",
        "full_name": "Acme",
        "followers": 3400,
        "category_name": "Restaurant",
        "external_url": "https://acme.example",
    },
}


@pytest.mark.parametrize("source", SOURCES)
def test_mapping_loads_and_is_unverified(source: str) -> None:
    mapping = load_mapping(source)
    assert mapping.source == source and mapping.version >= 1
    has_samples = (MAPPINGS_DIR.parents[2] / "tests" / "fixtures" / "brightdata" / "captured").is_dir()
    assert mapping.unverified is not has_samples


@pytest.mark.parametrize("source", SOURCES)
def test_normalization(source: str) -> None:
    mapping = load_mapping(source)
    out = normalize_record(mapping, RECORDS[source])
    assert out.external_id and out.entity == mapping.entity
    assert out.fields["name" if mapping.entity == "company" else "title"]
    assert "  " not in str(out.fields)


def test_linkedin_company_numbers() -> None:
    out = normalize_record(load_mapping("linkedin_companies"), RECORDS["linkedin_companies"])
    assert out.fields["name"] == "Acme SAS"
    assert out.fields["follower_count"] == 1200
    assert out.fields["employee_count"] == 45


@pytest.mark.parametrize("source", SOURCES)
def test_record_without_required_field_is_rejected(source: str) -> None:
    with pytest.raises(MappingError):
        normalize_record(load_mapping(source), {"id": "1", "job_posting_id": "1", "company_id": "1"})


def settings(mode: str) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "ingest_mode": mode,
        "bright_data_api_token": "token-for-tests",
        "bright_data_webhook_secret": "secret-for-tests",
        "public_webhook_base_url": "https://hooks.example",
    }
    values["source_maps_dataset_id"] = "gd_maps"
    values["source_computrabajo_dataset_id"] = "gd_jobs"
    for source in SOURCES:
        values[f"source_{source}_enabled"] = True
        values[f"source_{source}_dataset_id"] = f"gd_{source}"
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("source", SOURCES)
def test_build_connector_mock_and_live(source: str) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert isinstance(build_connector(settings("mock"), source, client=client), MockConnector)
    live = build_connector(settings("live"), source, client=client)
    assert isinstance(live, BrightDataConnector) and live.dataset_id == f"gd_{source}"
