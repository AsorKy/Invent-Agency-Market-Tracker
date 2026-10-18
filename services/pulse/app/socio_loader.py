"""Load socioeconomic indicator files for the Pulse opportunity index.

The loader reads DANE-style CSV files from the configured ``SOCIO_DATA_DIR``,
treats them as untrusted input, and returns records for ``libs/domain/socio.py``.
It relates to ``repository.py`` (which exposes the result) and REQ-SOC-001.
File names are checked against a strict pattern and resolved paths must stay
inside the data directory, which blocks traversal and symlink escapes.
"""

import csv
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from libs.domain.socio import IMD_FIELDS, INDICATOR_FIELDS, TerritoryRecord

_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.csv$")
_IDENTIFIERS = ("city", "territory_code", "territory_name")
_SHARE_FIELDS = ("formality_rate", "youth_employment_rate", *IMD_FIELDS)
MAX_FILE_BYTES = 5_000_000


class SocioDataError(ValueError):
    """Raised when the indicator directory or a file is invalid."""


def resolve_data_file(base_dir: str | Path, name: str) -> Path:
    """Return the safe resolved path of ``name`` inside ``base_dir``."""
    if not _FILE_NAME.fullmatch(name) or ".." in name:
        raise SocioDataError("invalid_file_name")
    base = Path(base_dir).resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise SocioDataError("path_outside_data_dir")
    return path


def _parse_row(row: dict[str, str | None], line: int, file_name: str) -> TerritoryRecord:
    """Validate one CSV row and convert it to a record."""
    where = f"{file_name}:{line}"
    ids = {key: (row.get(key) or "").strip() for key in _IDENTIFIERS}
    if not all(ids.values()):
        raise SocioDataError(f"missing_identifier {where}")
    values: dict[str, float] = {}
    for field in INDICATOR_FIELDS:
        try:
            value = float((row.get(field) or "").strip())
        except ValueError as exc:
            raise SocioDataError(f"invalid_number {field} {where}") from exc
        if not math.isfinite(value) or value < 0:
            raise SocioDataError(f"out_of_range {field} {where}")
        if field in _SHARE_FIELDS and value > 100:
            raise SocioDataError(f"out_of_range {field} {where}")
        values[field] = value
    return TerritoryRecord(
        city=ids["city"],
        territory_code=ids["territory_code"],
        territory_name=ids["territory_name"],
        indicators=values,
    )


def load_records(base_dir: str | Path) -> tuple[list[TerritoryRecord], datetime | None]:
    """Load every ``*.csv`` file in ``base_dir``; return records and newest mtime.

    A missing directory or no files yields no records and ``None``. Duplicate
    ``(city, territory_code)`` keys are rejected.
    """
    base = Path(base_dir).resolve()
    if not base.is_dir():
        return [], None
    records: list[TerritoryRecord] = []
    seen: set[tuple[str, str]] = set()
    newest: float | None = None
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        if not _FILE_NAME.fullmatch(entry.name):
            continue
        path = resolve_data_file(base, entry.name)
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise SocioDataError(f"unreadable_file {entry.name}")
        newest = max(newest or 0.0, path.stat().st_mtime)
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = [
                f for f in (*_IDENTIFIERS, *INDICATOR_FIELDS) if f not in (reader.fieldnames or [])
            ]
            if missing:
                raise SocioDataError(f"missing_columns {entry.name} {','.join(missing)}")
            for line, row in enumerate(reader, start=2):
                record = _parse_row(row, line, entry.name)
                key = (record.city, record.territory_code)
                if key in seen:
                    raise SocioDataError(f"duplicate_territory {entry.name}:{line}")
                seen.add(key)
                records.append(record)
    as_of = datetime.fromtimestamp(newest, UTC) if newest is not None else None
    return records, as_of
