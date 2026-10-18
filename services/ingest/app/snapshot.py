"""Hash snapshot payloads and detect which records changed.

Provides the canonical sha256 of a payload (sorted keys, compact JSON), a
stable identity per record, and ``changed_records`` that compares a snapshot
against the previous one for the same source and external id. Only new or
changed records may produce candidates and signals (REQ-ING-001). It relates
to ``services/ingest/app/main.py`` and ``repository.py``. It is pure Python
and performs no I/O.
"""

import hashlib
from typing import Any

import orjson

RECORD_ID_FIELDS = ("place_id", "id", "url", "nit")


def canonical_bytes(value: Any) -> bytes:
    """Serialize ``value`` with sorted keys so equal content gives equal bytes."""
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)


def payload_sha256(payload: Any) -> str:
    """Return the hex sha256 of the canonical payload."""
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def split_records(payload: Any) -> list[dict[str, Any]]:
    """Return the dict records of a payload (a list, or a single object)."""
    records = payload if isinstance(payload, list) else [payload]
    return [record for record in records if isinstance(record, dict)]


def record_identity(record: dict[str, Any]) -> str:
    """Return a stable identity: first known id field, else a hash of name and city."""
    for field in RECORD_ID_FIELDS:
        value = record.get(field)
        if isinstance(value, str | int) and str(value).strip():
            return f"{field}:{value}"
    name = record.get("name") or record.get("business_name") or record.get("title") or ""
    return "name:" + payload_sha256([str(name).strip().lower(), record.get("city")])


def changed_records(previous: Any | None, current: Any) -> list[dict[str, Any]]:
    """Return the records of ``current`` that are new or differ from ``previous``.

    With no previous payload every record counts as new.
    """
    seen: dict[str, str] = {}
    if previous is not None:
        seen = {record_identity(r): payload_sha256(r) for r in split_records(previous)}
    return [
        record
        for record in split_records(current)
        if seen.get(record_identity(record)) != payload_sha256(record)
    ]
