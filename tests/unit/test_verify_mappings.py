"""Tests for ops/verify_mappings.py using synthetic and temporary samples."""

import json
import shutil
from pathlib import Path

import pytest

from ops import verify_mappings as vm
from services.ingest.app.mapping import MAPPINGS_DIR, load_mapping

SYNTHETIC = Path(__file__).resolve().parents[1] / "fixtures" / "brightdata" / "synthetic"
SOURCES = ["computrabajo", "instagram", "linkedin_companies", "linkedin_jobs", "maps"]


@pytest.fixture
def mappings(tmp_path: Path) -> Path:
    target = tmp_path / "mappings"
    shutil.copytree(MAPPINGS_DIR, target)
    return target


def _write(directory: Path, source: str, records: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{source}.json").write_text(json.dumps(records), encoding="utf-8")


def test_all_sources_have_synthetic_fixture_and_stay_unverified() -> None:
    assert vm.list_sources(MAPPINGS_DIR) == SOURCES
    for source in SOURCES:
        records, synthetic = vm.load_records(SYNTHETIC / f"{source}.json")
        assert synthetic and records
        assert all(r["_synthetic"] is True for r in records)
        assert load_mapping(source).unverified is True
        report = vm.check_source(load_mapping(source), records)
        assert report.coverage >= 0.8
        assert report.unmapped and not report.missing_required and not report.type_failures


def test_report_flags_missing_required_type_failure_and_low_coverage() -> None:
    mapping = load_mapping("maps")
    records = [{"place_id": "x", "rating": "n/a", "extra": 1}]
    report = vm.check_source(mapping, records)
    assert report.missing_required == ["name"]
    assert report.type_failures == ["rating (float)"]
    assert report.unmapped == ["extra"]
    assert report.coverage < 0.8


def test_nested_paths_count_as_mapped() -> None:
    mapping = load_mapping("maps")
    report = vm.check_source(mapping, [{"name": "A", "location": {"lat": 1.0, "lng": 2.0}}])
    assert report.unmapped == []


def test_cli_exit_code_follows_min_coverage(tmp_path: Path, mappings: Path) -> None:
    _write(tmp_path / "s", "maps", [{"place_id": "x", "name": "A"}])
    base = ["--samples", str(tmp_path / "s"), "--source", "maps", "--mappings-dir", str(mappings)]
    assert vm.main(base) == 1
    assert vm.main([*base, "--min-coverage", "0.1"]) == 0


def test_missing_samples_fail(tmp_path: Path, mappings: Path) -> None:
    (tmp_path / "empty").mkdir()
    args = ["--samples", str(tmp_path / "empty"), "--mappings-dir", str(mappings)]
    assert vm.main(args) == 1


def test_apply_refuses_synthetic_samples(mappings: Path) -> None:
    code = vm.main(["--samples", str(SYNTHETIC), "--apply", "--mappings-dir", str(mappings)])
    assert code == 1
    for source in SOURCES:
        assert load_mapping(source, mappings).unverified is True


def test_apply_flips_only_real_passing_source_and_keeps_formatting(
    tmp_path: Path, mappings: Path
) -> None:
    records = json.loads((SYNTHETIC / "maps.json").read_text(encoding="utf-8"))
    for record in records:
        del record["_synthetic"]
    _write(tmp_path / "real", "maps", records)
    before = (mappings / "maps.yaml").read_text(encoding="utf-8")
    args = ["--samples", str(tmp_path / "real"), "--source", "maps", "--apply"]
    assert vm.main([*args, "--mappings-dir", str(mappings)]) == 0
    after = (mappings / "maps.yaml").read_text(encoding="utf-8")
    assert after == before.replace("unverified: true\n", "unverified: false\n", 1)
    assert load_mapping("maps", mappings).unverified is False
    assert load_mapping("computrabajo", mappings).unverified is True


def test_apply_skips_low_coverage(tmp_path: Path, mappings: Path) -> None:
    _write(tmp_path / "real", "maps", [{"place_id": "x", "name": "A"}])
    args = ["--samples", str(tmp_path / "real"), "--source", "maps", "--apply"]
    vm.main([*args, "--mappings-dir", str(mappings)])
    assert load_mapping("maps", mappings).unverified is True


def test_capture_limits_and_redacts(tmp_path: Path, mappings: Path) -> None:
    records = [
        {"place_id": f"p{i}", "name": "A", "address": f"owner{i}@gmail.com 12345678-9"}
        for i in range(5)
    ]
    _write(tmp_path / "real", "maps", records)
    out = tmp_path / "captured"
    code = vm.main(
        [
            "--samples",
            str(tmp_path / "real"),
            "--source",
            "maps",
            "--capture",
            "--min-coverage",
            "0",
            "--captured-dir",
            str(out),
            "--mappings-dir",
            str(mappings),
        ]
    )
    assert code == 0
    saved = json.loads((out / "maps.json").read_text(encoding="utf-8"))
    assert len(saved) == 3
    assert saved[0]["address"] == "c***@gmail.com ***-***-***-9"


def test_capture_refuses_synthetic(tmp_path: Path, mappings: Path) -> None:
    out = tmp_path / "captured"
    code = vm.main(
        [
            "--samples",
            str(SYNTHETIC),
            "--capture",
            "--captured-dir",
            str(out),
            "--mappings-dir",
            str(mappings),
        ]
    )
    assert code == 1
    assert not out.exists()
