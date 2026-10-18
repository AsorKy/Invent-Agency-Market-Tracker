"""Unit tests for source connectors and YAML mappings (REQ-ING-004).

Covers mapping validation, record normalization for maps and computrabajo,
the shipped mapping files (all flagged ``unverified`` until recorded samples
exist, D-13), and the mock connector driven against the Flask mock through a
WSGI transport. No network is used.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("flask")

from services.ingest.app.connectors import (  # noqa: E402
    ConnectorError,
    MockConnector,
    SourceConnector,
)
from services.ingest.app.mapping import (  # noqa: E402
    MAPPINGS_DIR,
    MappingError,
    load_mapping,
    normalize_record,
    parse_mapping,
)
from services.mocks.app.main import create_app  # noqa: E402


def make_connector(source: str, **app_options: Any) -> MockConnector:
    client = httpx.Client(
        transport=httpx.WSGITransport(app=create_app(**app_options)),
        base_url="http://mock.local",
    )
    return MockConnector(source, client, dataset_id=f"mock-{source}")


@pytest.mark.parametrize("source", ["maps", "computrabajo"])
def test_shipped_mappings_load_and_are_marked_unverified(source: str) -> None:
    mapping = load_mapping(source)
    assert mapping.source == source and mapping.version >= 1
    has_samples = (MAPPINGS_DIR.parents[2] / "tests" / "fixtures" / "brightdata").is_dir()
    assert mapping.unverified is not has_samples


def test_maps_normalization() -> None:
    record = {
        "place_id": "p1",
        "name": "  Cafe Uno ",
        "category": "Restaurant",
        "open_website": "https://cafe.example",
        "rating": "4,5",
        "reviews_count": "1,200",
        "lat": 6.2,
        "lon": -75.5,
    }
    out = normalize_record(load_mapping("maps"), record)
    assert out.external_id == "p1"
    assert out.fields["name"] == "Cafe Uno"
    assert out.fields["category"] == "restaurant"
    assert out.fields["rating"] == 4.5
    assert out.fields["review_count"] == 1200
    assert out.fields["lng"] == -75.5
    assert out.fields["phone"] is None
    assert out.mapping_version == 1


def test_missing_required_field_or_id_is_rejected() -> None:
    mapping = load_mapping("maps")
    with pytest.raises(MappingError, match="required field 'name'"):
        normalize_record(mapping, {"place_id": "p1"})
    with pytest.raises(MappingError, match="external id"):
        normalize_record(mapping, {"name": "x"})


def test_bad_optional_value_becomes_none_but_bad_required_fails() -> None:
    mapping = load_mapping("maps")
    out = normalize_record(mapping, {"place_id": "p", "name": "n", "rating": "abc"})
    assert out.fields["rating"] is None
    strict = parse_mapping(
        {
            "version": 1,
            "source": "s",
            "entity": "company",
            "signal_type": "t",
            "external_id": ["id"],
            "fields": {"site": {"from": ["u"], "transform": "url", "required": True}},
        }
    )
    with pytest.raises(MappingError):
        normalize_record(strict, {"id": "1", "u": "ftp://x"})


def test_computrabajo_normalization_uses_fallback_paths() -> None:
    out = normalize_record(
        load_mapping("computrabajo"),
        {"id": 77, "job_title": "Dev", "company_name": "Acme", "job_url": "https://x.example/1"},
    )
    assert out.external_id == "77"
    assert out.entity == "job_posting"
    assert out.fields["title"] == "Dev" and out.fields["url"] == "https://x.example/1"


def test_nested_paths_resolve() -> None:
    out = normalize_record(
        load_mapping("maps"),
        {"place_id": "p", "name": "n", "location": {"lat": "1.5", "lng": 2}},
    )
    assert out.fields["lat"] == 1.5 and out.fields["lng"] == 2.0


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": 0},
        {"entity": "robot"},
        {"fields": {}},
        {"external_id": []},
        {"fields": {"a": {"from": ["x"], "transform": "bogus"}}},
        {"unverified": "yes"},
    ],
)
def test_invalid_mapping_is_rejected(mutation: dict[str, Any]) -> None:
    data: dict[str, Any] = {
        "version": 1,
        "source": "s",
        "entity": "company",
        "signal_type": "t",
        "external_id": ["id"],
        "fields": {"a": {"from": ["x"]}},
    }
    parse_mapping(data)
    with pytest.raises(MappingError):
        parse_mapping({**data, **mutation})


def test_load_mapping_rejects_unsafe_or_unknown_source(tmp_path: Path) -> None:
    with pytest.raises(MappingError):
        load_mapping("../maps")
    with pytest.raises(MappingError):
        load_mapping("nonexistent", directory=tmp_path)
    (tmp_path / "bad.yaml").write_text("source: other\n", encoding="utf-8")
    with pytest.raises(MappingError):
        load_mapping("bad", directory=tmp_path)


def test_mock_connector_full_cycle_for_maps() -> None:
    connector = make_connector("maps", records_per_snapshot=3)
    assert isinstance(connector, SourceConnector)
    result = connector.trigger([{"keyword": "restaurants"}], notify_url="http://i.local/hook")
    assert connector.progress(result.snapshot_id).status == "ready"
    records = connector.fetch(result.snapshot_id)
    assert len(records) == 3
    normalized = [connector.normalize(record) for record in records]
    assert all(item.fields["name"].startswith("Synthetic") for item in normalized)
    assert len({item.external_id for item in normalized}) == 3


def test_mock_connector_full_cycle_for_computrabajo() -> None:
    connector = make_connector("computrabajo", records_per_snapshot=2)
    snapshot_id = connector.trigger([{"url": "https://x.example"}]).snapshot_id
    normalized = [connector.normalize(r) for r in connector.fetch(snapshot_id)]
    assert [item.entity for item in normalized] == ["job_posting", "job_posting"]


def test_mock_connector_maps_provider_failures_to_retryable_errors() -> None:
    connector = make_connector("maps", failure_rate=1.0)
    with pytest.raises(ConnectorError) as info:
        connector.trigger([])
    assert info.value.retryable is True


def test_mock_connector_unknown_snapshot_is_not_retryable() -> None:
    connector = make_connector("maps")
    with pytest.raises(ConnectorError) as info:
        connector.fetch("missing")
    assert info.value.retryable is False
