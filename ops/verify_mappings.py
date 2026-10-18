"""Verify source mapping files against provider sample records.

``python ops/verify_mappings.py --samples DIR`` runs every mapping in
``libs/domain/source_mappings/`` over the sample files of its source
(``<source>.json`` or ``<source>.<label>.json``; each file is a JSON list of
records, or an object with a ``records`` list). For each source it reports
provider fields present in the samples that no mapping path uses, required
mapped fields missing from the samples, type (transform) failures, and a
coverage ratio: resolved mapped fields divided by mapped fields over all
records. The exit code is non-zero when any source is below
``--min-coverage`` (default 0.8) or has no samples.

``--capture`` copies up to 3 redacted records per source into
``tests/fixtures/brightdata/captured/`` using ``libs/common/log_redaction.py``;
``make verify-live`` calls it after the first real snapshot (decision D-13,
REQ-RUN-006). ``--apply`` flips ``unverified: true`` to ``false`` in a mapping
file by a targeted text edit, only for a source that meets coverage and whose
samples are all real or captured. Records carrying ``"_synthetic": true`` (see
``tests/fixtures/brightdata/synthetic/``) exist only to test this harness: they
never count as verification, and ``--apply`` refuses them. The mapping engine
is ``services/ingest/app/mapping.py``.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from libs.common.log_redaction import redact_text  # noqa: E402
from services.ingest.app.mapping import (  # noqa: E402
    MAPPINGS_DIR,
    MappingError,
    SourceMapping,
    _first,
    _lookup,
    _transform,
    load_mapping,
)

SYNTHETIC_KEY = "_synthetic"
CAPTURE_LIMIT = 3
DEFAULT_CAPTURE_DIR = REPOSITORY / "tests" / "fixtures" / "brightdata" / "captured"


@dataclass
class SourceReport:
    """Verification result for one source."""

    source: str
    records: int = 0
    synthetic: bool = False
    coverage: float = 0.0
    unmapped: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    type_failures: list[str] = field(default_factory=list)


def list_sources(directory: Path) -> list[str]:
    """Return source names that have a mapping file in ``directory``."""
    return sorted(p.stem for p in directory.glob("*.yaml"))


def sample_files(samples: Path, source: str) -> list[Path]:
    """Return the sample files of ``source`` inside ``samples``."""
    found = [samples / f"{source}.json"] if (samples / f"{source}.json").is_file() else []
    found.extend(sorted(samples.glob(f"{source}.*.json")))
    return found


def load_records(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Read a sample file; return its dict records and whether it is synthetic."""
    data = json.loads(path.read_text(encoding="utf-8"))
    synthetic = False
    if isinstance(data, dict):
        synthetic = data.get(SYNTHETIC_KEY) is True
        data = data.get("records", [])
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: expected a list of records")
    records = [r for r in data if isinstance(r, dict)]
    synthetic = synthetic or any(r.get(SYNTHETIC_KEY) is True for r in records)
    return records, synthetic


def _leaf_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    for key, item in value.items():
        if key.startswith("_"):
            continue
        path = f"{prefix}{key}"
        if isinstance(item, dict) and item:
            paths.extend(_leaf_paths(item, f"{path}."))
        else:
            paths.append(path)
    return paths


def _mapped_paths(mapping: SourceMapping) -> set[str]:
    paths = set(mapping.external_id)
    for rule in mapping.fields.values():
        paths.update(rule.sources)
    return paths


def _is_mapped(leaf: str, mapped: set[str]) -> bool:
    return any(leaf == p or p.startswith(f"{leaf}.") or leaf.startswith(f"{p}.") for p in mapped)


def check_source(mapping: SourceMapping, records: list[dict[str, Any]]) -> SourceReport:
    """Run ``mapping`` over ``records`` and collect the verification report."""
    report = SourceReport(source=mapping.source, records=len(records))
    mapped = _mapped_paths(mapping)
    unmapped: set[str] = set()
    missing: set[str] = set()
    failures: set[str] = set()
    resolved = 0
    for record in records:
        unmapped.update(p for p in _leaf_paths(record) if not _is_mapped(p, mapped))
        for name, rule in mapping.fields.items():
            value = _first(record, rule.sources)
            if value is None:
                if rule.required and not any(_lookup(record, p) for p in rule.sources):
                    missing.add(name)
                continue
            try:
                _transform(value, rule.transform)
            except MappingError:
                failures.add(f"{name} ({rule.transform})")
                continue
            resolved += 1
    total = len(records) * len(mapping.fields)
    report.coverage = resolved / total if total else 0.0
    report.unmapped = sorted(unmapped)
    report.missing_required = sorted(missing)
    report.type_failures = sorted(failures)
    return report


def redact_value(value: Any) -> Any:
    """Redact every string inside a JSON value."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    return value


def capture(records: list[dict[str, Any]], source: str, directory: Path) -> int:
    """Write up to ``CAPTURE_LIMIT`` redacted records for ``source``."""
    kept = [redact_value(r) for r in records[:CAPTURE_LIMIT]]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{source}.json"
    target.write_text(json.dumps(kept, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return len(kept)


_UNVERIFIED_LINE = re.compile(r"^(unverified:\s*)true(\s*(#.*)?)$", re.MULTILINE)


def flip_unverified(path: Path) -> bool:
    """Set ``unverified: false`` with a targeted text edit; True if it changed."""
    text = path.read_text(encoding="utf-8")
    new_text, count = _UNVERIFIED_LINE.subn(r"\1false\2", text, count=1)
    if count:
        path.write_text(new_text, encoding="utf-8")
    return bool(count)


def _print_report(report: SourceReport, min_coverage: float) -> None:
    label = "synthetic" if report.synthetic else "real"
    status = "OK" if report.coverage >= min_coverage else "LOW"
    print(
        f"[{status}] {report.source}: {report.records} records ({label}), "
        f"coverage {report.coverage:.2f}"
    )
    print(f"  unmapped fields: {', '.join(report.unmapped) or '-'}")
    print(f"  missing required: {', '.join(report.missing_required) or '-'}")
    print(f"  type failures: {', '.join(report.type_failures) or '-'}")


def run(args: argparse.Namespace) -> int:
    """Execute the requested actions and return the process exit code."""
    mappings_dir: Path = args.mappings_dir
    samples: Path = args.samples
    if not samples.is_dir():
        print(f"samples directory not found: {samples}", file=sys.stderr)
        return 2
    sources = args.source or list_sources(mappings_dir)
    exit_code = 0
    for source in sources:
        try:
            mapping = load_mapping(source, mappings_dir)
            files = sample_files(samples, source)
            records: list[dict[str, Any]] = []
            synthetic = False
            for path in files:
                file_records, file_synthetic = load_records(path)
                records.extend(file_records)
                synthetic = synthetic or file_synthetic
        except (MappingError, ValueError) as exc:
            print(f"[FAIL] {source}: {exc}", file=sys.stderr)
            exit_code = 1
            continue
        if not records:
            print(f"[FAIL] {source}: no sample records in {samples}", file=sys.stderr)
            exit_code = 1
            continue
        report = check_source(mapping, records)
        report.synthetic = synthetic
        _print_report(report, args.min_coverage)
        passed = report.coverage >= args.min_coverage
        if not passed:
            exit_code = 1
        if args.capture:
            if synthetic:
                print(f"  capture refused: {source} samples are synthetic", file=sys.stderr)
                exit_code = 1
            else:
                count = capture(records, source, args.captured_dir)
                print(f"  captured {count} redacted records")
        if args.apply:
            if synthetic:
                print(f"  apply refused: {source} samples are synthetic", file=sys.stderr)
                exit_code = 1
            elif not passed:
                print(f"  apply skipped: {source} is below min coverage", file=sys.stderr)
            elif not mapping.unverified:
                print("  already verified")
            elif flip_unverified(mappings_dir / f"{source}.yaml"):
                print(f"  {source} marked unverified: false")
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(description="Verify source mappings against samples.")
    parser.add_argument("--samples", type=Path, required=True, help="directory with samples")
    parser.add_argument("--source", action="append", help="limit to a source (repeatable)")
    parser.add_argument("--min-coverage", type=float, default=0.8)
    parser.add_argument("--capture", action="store_true", help="store redacted records")
    parser.add_argument("--captured-dir", type=Path, default=DEFAULT_CAPTURE_DIR)
    parser.add_argument("--apply", action="store_true", help="flip unverified for passing sources")
    parser.add_argument("--mappings-dir", type=Path, default=MAPPINGS_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run."""
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
