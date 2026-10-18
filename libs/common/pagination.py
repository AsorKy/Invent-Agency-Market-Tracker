"""Encode and decode opaque keyset pagination cursors.

The cursor stores the last row's ``updated_at`` and UUID so repositories can
continue with a stable ``(updated_at, id)`` ordering. It relates to
``services/core/app/repository.py`` and the company/lead API handlers. Its
purpose is to keep page cost independent of the number of skipped rows and to
avoid exposing database-specific pagination fields to clients.
"""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PageCursor:
    updated_at: datetime
    item_id: UUID


def encode_cursor(updated_at: datetime, item_id: UUID) -> str:
    """Return a URL-safe opaque cursor for one ordered row."""
    payload = json.dumps(
        {"updated_at": updated_at.isoformat(), "item_id": str(item_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_cursor(value: str) -> PageCursor:
    """Decode a cursor or raise ``ValueError`` for malformed input."""
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        updated_at = datetime.fromisoformat(payload["updated_at"])
        item_id = UUID(payload["item_id"])
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid_cursor") from exc
    if updated_at.tzinfo is None:
        raise ValueError("invalid_cursor")
    return PageCursor(updated_at=updated_at, item_id=item_id)
