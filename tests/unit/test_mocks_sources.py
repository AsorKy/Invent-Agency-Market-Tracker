"""Tests that the Bright Data mock emits records for every source (REQ-ING-007, REQ-ING-004).

Record shapes are compared with ``tests/fixtures/brightdata/synthetic/`` and
checked against the mapping verifier in ``ops/verify_mappings.py``.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("flask")

from ops import verify_mappings as vm  # noqa: E402
from services.ingest.app.mapping import MAPPINGS_DIR, load_mapping  # noqa: E402
from services.mocks.app.main import create_app  # noqa: E402

SYNTHETIC = Path(__file__).resolve().parents[1] / "fixtures" / "brightdata" / "synthetic"
SOURCES = ["maps", "computrabajo", "linkedin_companies", "linkedin_jobs", "instagram"]


def _snapshot(source: str, count: int = 3) -> list[dict]:
    client = create_app(records_per_snapshot=count).test_client()
    resp = client.post(
        "/datasets/v3/trigger", query_string={"dataset_id": f"mock-{source}"}, json=[{"q": "x"}]
    )
    snap_id = resp.get_json()["snapshot_id"]
    return client.get(f"/datasets/v3/snapshot/{snap_id}").get_json()


@pytest.mark.parametrize("source", SOURCES)
def test_fields_match_synthetic_fixture(source: str) -> None:
    fixture = json.loads((SYNTHETIC / f"{source}.json").read_text(encoding="utf-8"))
    expected = set(fixture[0]) - {"_synthetic"}
    records = _snapshot(source)
    assert len(records) == 3
    assert all(set(r) == expected for r in records)


@pytest.mark.parametrize("source", SOURCES)
def test_records_are_unique_and_deterministic_per_snapshot(source: str) -> None:
    records = _snapshot(source, 5)
    id_field = {
        "maps": "place_id",
        "computrabajo": "job_id",
        "linkedin_companies": "company_id",
        "linkedin_jobs": "job_posting_id",
        "instagram": "id",
    }[source]
    ids = [r[id_field] for r in records]
    assert len(set(ids)) == 5


@pytest.mark.parametrize("source", SOURCES)
def test_mapping_verifier_accepts_mock_records(source: str) -> None:
    mapping = load_mapping(source, MAPPINGS_DIR)
    report = vm.check_source(mapping, _snapshot(source))
    assert not report.missing_required
    assert not report.type_failures


def test_hyphenated_dataset_ids_resolve_the_source() -> None:
    client = create_app(records_per_snapshot=1).test_client()
    resp = client.post("/datasets/v3/trigger", query_string={"dataset_id": "linkedin-jobs-co"})
    records = client.get(f"/datasets/v3/snapshot/{resp.get_json()['snapshot_id']}").get_json()
    assert "job_posting_id" in records[0]
