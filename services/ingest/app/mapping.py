"""Load YAML source mapping files and normalize provider records.

Mapping files live in ``libs/domain/source_mappings/<source>.yaml`` and turn
provider fields into canonical fields (REQ-ING-004); the format is described
in that folder's README. ``normalize_record`` is pure: it reads a provider
record and returns a ``NormalizedRecord`` or raises ``MappingError``. It is
used by ``services/ingest/app/connectors.py`` and, later, the processor.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

MAPPINGS_DIR = Path(__file__).resolve().parents[3] / "libs" / "domain" / "source_mappings"
TRANSFORMS = frozenset({"strip", "lower", "int", "float", "digits", "url"})
ENTITIES = frozenset({"company", "job_posting"})


class MappingError(ValueError):
    """Raised when a mapping file is invalid or a record fails mapping."""


@dataclass(frozen=True)
class FieldRule:
    """One canonical field: provider paths, transform, and requirement."""

    sources: tuple[str, ...]
    transform: str | None = None
    required: bool = False
    default: Any = None


@dataclass(frozen=True)
class SourceMapping:
    """A validated mapping file."""

    source: str
    version: int
    unverified: bool
    entity: str
    signal_type: str
    external_id: tuple[str, ...]
    fields: dict[str, FieldRule] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedRecord:
    """A provider record expressed in canonical fields."""

    source: str
    external_id: str
    entity: str
    signal_type: str
    mapping_version: int
    fields: dict[str, Any]


def _path_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise MappingError(f"{where} must be a non-empty list of strings")
    return tuple(value)


def parse_mapping(data: Any, *, expected_source: str | None = None) -> SourceMapping:
    """Validate parsed YAML ``data`` and build a ``SourceMapping``."""
    if not isinstance(data, dict):
        raise MappingError("mapping must be a YAML object")
    source = data.get("source")
    if not isinstance(source, str) or not source:
        raise MappingError("mapping.source is required")
    if expected_source is not None and source != expected_source:
        raise MappingError(f"mapping.source {source!r} does not match {expected_source!r}")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise MappingError("mapping.version must be a positive integer")
    unverified = data.get("unverified", False)
    if not isinstance(unverified, bool):
        raise MappingError("mapping.unverified must be a boolean")
    entity = data.get("entity")
    if entity not in ENTITIES:
        raise MappingError(f"mapping.entity must be one of {sorted(ENTITIES)}")
    signal_type = data.get("signal_type")
    if not isinstance(signal_type, str) or not signal_type:
        raise MappingError("mapping.signal_type is required")
    raw_fields = data.get("fields")
    if not isinstance(raw_fields, dict) or not raw_fields:
        raise MappingError("mapping.fields must be a non-empty object")
    rules: dict[str, FieldRule] = {}
    for name, raw in raw_fields.items():
        if not isinstance(raw, dict):
            raise MappingError(f"fields.{name} must be an object")
        transform = raw.get("transform")
        if transform is not None and transform not in TRANSFORMS:
            raise MappingError(f"fields.{name}.transform {transform!r} is unknown")
        rules[str(name)] = FieldRule(
            sources=_path_list(raw.get("from"), f"fields.{name}.from"),
            transform=transform,
            required=bool(raw.get("required", False)),
            default=raw.get("default"),
        )
    return SourceMapping(
        source=source,
        version=version,
        unverified=unverified,
        entity=str(entity),
        signal_type=signal_type,
        external_id=_path_list(data.get("external_id"), "mapping.external_id"),
        fields=rules,
    )


def load_mapping(source: str, directory: Path | None = None) -> SourceMapping:
    """Load and validate ``<source>.yaml`` from the mappings directory."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", source):
        raise MappingError(f"invalid source name {source!r}")
    path = (directory or MAPPINGS_DIR) / f"{source}.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MappingError(f"cannot read mapping for {source!r}: {exc.strerror}") from exc
    except yaml.YAMLError as exc:
        raise MappingError(f"invalid YAML in mapping for {source!r}") from exc
    return parse_mapping(data, expected_source=source)


def _lookup(record: dict[str, Any], path: str) -> Any:
    """Return the first non-empty value at a dotted ``path``, else None."""
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if current is None or (isinstance(current, str) and not current.strip()):
        return None
    return current


def _first(record: dict[str, Any], paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _lookup(record, path)
        if value is not None:
            return value
    return None


def _transform(value: Any, name: str | None) -> Any:
    if name is None:
        return value
    try:
        if name == "strip":
            return str(value).strip()
        if name == "lower":
            return str(value).strip().lower()
        if name == "int":
            return int(float(str(value).replace(",", "").strip()))
        if name == "float":
            return float(str(value).replace(",", ".").strip())
        if name == "digits":
            return re.sub(r"\D", "", str(value))
        text = str(value).strip()  # url
        if not re.match(r"https?://", text, re.IGNORECASE):
            raise ValueError("not an http url")
        return text
    except (ValueError, TypeError) as exc:
        raise MappingError(f"cannot apply {name} to {value!r}") from exc


def normalize_record(mapping: SourceMapping, record: dict[str, Any]) -> NormalizedRecord:
    """Map one provider ``record`` to canonical fields or raise ``MappingError``."""
    external_id = _first(record, mapping.external_id)
    if external_id is None:
        raise MappingError("record has no external id")
    out: dict[str, Any] = {}
    for name, rule in mapping.fields.items():
        value = _first(record, rule.sources)
        if value is None:
            value = rule.default
        if value is None:
            if rule.required:
                raise MappingError(f"required field {name!r} is missing")
            out[name] = None
            continue
        try:
            out[name] = _transform(value, rule.transform)
        except MappingError:
            if rule.required:
                raise
            out[name] = None
    return NormalizedRecord(
        source=mapping.source,
        external_id=str(external_id).strip(),
        entity=mapping.entity,
        signal_type=mapping.signal_type,
        mapping_version=mapping.version,
        fields=out,
    )
