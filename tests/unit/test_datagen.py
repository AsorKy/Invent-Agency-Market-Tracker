"""Tests for deterministic synthetic data generation (REQ-DAT-008)."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from datagen.builder import build_dataset, build_snapshots


def _hash(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def test_same_seed_is_identical_and_other_seed_differs() -> None:
    kw = {"city": "all", "orgs": 200, "days": 180}
    assert _hash(build_dataset(**kw, seed=42)) == _hash(build_dataset(**kw, seed=42))
    assert _hash(build_dataset(**kw, seed=42)) != _hash(build_dataset(**kw, seed=43))


def test_default_shape() -> None:
    data = build_dataset(city="all", orgs=200, days=180, seed=42)
    assert len(data["companies"]) == 200
    assert {c["city"] for c in data["companies"]} == {"Medellin", "Bogota", "Cali"}
    assert 1300 <= len(data["signals"]) <= 1700
    assert len(data["leads"]) == 50
    assert all(lead["outcome"] for lead in data["leads"])


def test_snapshots_shape_and_determinism() -> None:
    data = build_dataset(city="all", orgs=200, days=180, seed=42)
    snaps = build_snapshots(data, seed=42)
    assert snaps == build_snapshots(data, seed=42)
    assert len(snaps["maps"]) == 200
    assert snaps["computrabajo"]
    assert {"place_id", "name", "rating"} <= set(snaps["maps"][0])
    assert {"job_id", "job_title", "company_name"} <= set(snaps["computrabajo"][0])


def test_cli_output_files_byte_identical(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    digests = []
    for run in ("a", "b"):
        out = tmp_path / run
        subprocess.run(
            [
                sys.executable,
                "-m",
                "datagen",
                "build",
                "--city",
                "all",
                "--orgs",
                "200",
                "--days",
                "180",
                "--seed",
                "42",
                "--as-snapshots",
                "--output",
                str(out),
            ],
            check=True,
            cwd=root,
            capture_output=True,
        )
        digests.append(
            {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir())}
        )
    assert digests[0] == digests[1]
    assert "snapshot_maps.json" in digests[0]
    assert "snapshot_computrabajo.json" in digests[0]
