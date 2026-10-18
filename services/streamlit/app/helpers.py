"""Provide pure, Streamlit-free helpers for the reporting app.

The module holds ``safe_fetch``, freshness and stale logic, role gating, export
visibility, filter and query-param handling, and the Parquet reader. Keeping it
free of a Streamlit import makes it unit-testable without a server. It relates
to ``services/streamlit/app/ui.py``, which renders these results, and to
``Home.py``. The service has no database credentials: it only reads Parquet
from a shared volume and calls the gateway API.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, NamedTuple

import httpx

AS_OF_HEADER = "X-As-Of"
DEFAULT_GATEWAY_URL = "http://gateway:8000"
DEFAULT_PARQUET_DIR = "/data/parquet"
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_STALE_AFTER = timedelta(minutes=15)
COT = timedelta(hours=-5)

EXPORT_ROLES = frozenset({"admin", "analyst"})
KNOWN_ROLES = frozenset({"admin", "strategist", "intake", "bd_rep", "analyst"})
GENERIC_ERROR = "We could not load this"
NO_ACCESS = "You do not have access to this view"

FetchFn = Callable[[str, float], tuple[int, Mapping[str, str], Any]]


class Fetched(NamedTuple):
    """Result of ``safe_fetch``: ``(data, error)`` plus freshness metadata."""

    data: Any | None
    error: str | None
    as_of: datetime | None = None
    stale: bool = False


def gateway_url(path: str, env: Mapping[str, str] | None = None) -> str:
    """Join a gateway-relative API path to the configured base URL."""
    source = os.environ if env is None else env
    base = source.get("GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def parse_as_of(value: object) -> datetime | None:
    """Parse an ISO 8601 timestamp to an aware datetime, or None if invalid."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def format_as_of(as_of: datetime | None) -> str:
    """Render the card freshness caption in Colombia time (COT, UTC-5)."""
    if as_of is None:
        return "Data time unknown"
    local = as_of.astimezone(UTC) + COT
    return f"Data as of {local:%Y-%m-%d %H:%M} COT"


def is_stale(
    as_of: datetime | None,
    flag: object = False,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_STALE_AFTER,
) -> bool:
    """Return True when the body flag is set or the data age exceeds ``max_age``."""
    if flag is True:
        return True
    if as_of is None:
        return False
    current = now or datetime.now(UTC)
    return current - as_of > max_age


def _http_fetch(url: str, timeout: float) -> tuple[int, Mapping[str, str], Any]:
    response = httpx.get(url, timeout=timeout)
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    return response.status_code, response.headers, body


def safe_fetch(
    url: str,
    *,
    fetch: FetchFn | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_STALE_AFTER,
) -> Fetched:
    """Load one card query and never raise.

    Returns ``Fetched(data, error, as_of, stale)``. ``as_of`` comes from the
    ``X-As-Of`` header, falling back to an ``as_of`` field in the body; the
    ``stale`` body flag is honored. Failures return a generic error string
    without internal detail, so one failing card cannot break the others.
    """
    runner = fetch or _http_fetch
    try:
        status, headers, body = runner(url, timeout)
    except (httpx.HTTPError, OSError):
        return Fetched(None, GENERIC_ERROR)
    if status >= 400:
        return Fetched(None, GENERIC_ERROR)
    lowered = {key.lower(): value for key, value in headers.items()}
    as_of = parse_as_of(lowered.get(AS_OF_HEADER.lower()))
    flag: object = False
    if isinstance(body, dict):
        flag = body.get("stale", False)
        if as_of is None:
            as_of = parse_as_of(body.get("as_of"))
    return Fetched(body, None, as_of, is_stale(as_of, flag, now=now, max_age=max_age))


def current_role(state: Mapping[str, Any]) -> str | None:
    """Return the known role stored in session state, or None."""
    role = state.get("role")
    return role if isinstance(role, str) and role in KNOWN_ROLES else None


def has_access(state: Mapping[str, Any], allowed: Sequence[str] | None = None) -> bool:
    """Check the session role against a page allow-list (None allows any known role)."""
    role = current_role(state)
    if role is None:
        return False
    return allowed is None or role in allowed


def can_export(state: Mapping[str, Any]) -> bool:
    """Exports are visible only to admin and analyst."""
    role = current_role(state)
    return role is not None and role in EXPORT_ROLES


@dataclass(frozen=True)
class Filters:
    """Global dashboard filters shared through session state and the URL."""

    city: str = "all"
    service_line: str = "all"
    date_from: date | None = None
    date_to: date | None = None


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def filters_from_params(params: Mapping[str, str]) -> Filters:
    """Build filters from query params, ignoring invalid values."""
    start = _parse_date(params.get("from"))
    end = _parse_date(params.get("to"))
    if start and end and start > end:
        start = end = None
    return Filters(
        city=(params.get("city") or "all")[:64],
        service_line=(params.get("line") or "all")[:64],
        date_from=start,
        date_to=end,
    )


def filters_to_params(filters: Filters) -> dict[str, str]:
    """Serialize non-default filters for ``st.query_params``."""
    params: dict[str, str] = {}
    if filters.city != "all":
        params["city"] = filters.city
    if filters.service_line != "all":
        params["line"] = filters.service_line
    if filters.date_from:
        params["from"] = filters.date_from.isoformat()
    if filters.date_to:
        params["to"] = filters.date_to.isoformat()
    return params


def sync_filters(state: MutableMapping[str, Any], params: MutableMapping[str, str]) -> Filters:
    """Load filters from the URL on first run, then write them back to the URL.

    Session state wins once set so widget changes persist across reruns; the
    URL is rewritten from state so links stay shareable.
    """
    filters = state.get("filters")
    if not isinstance(filters, Filters):
        filters = filters_from_params(params)
        state["filters"] = filters
    wanted = filters_to_params(filters)
    for key in ("city", "line", "from", "to"):
        if key not in wanted and key in params:
            del params[key]
    for key, value in wanted.items():
        if params.get(key) != value:
            params[key] = value
    return filters


def read_parquet(name: str, base_dir: str | None = None) -> list[dict[str, Any]]:
    """Read a Parquet file from the shared volume as a list of row dicts.

    ``name`` must be a plain file stem; path separators are rejected so callers
    cannot escape the volume.
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("invalid parquet name")
    root = base_dir or os.environ.get("PARQUET_DIR", DEFAULT_PARQUET_DIR)
    pq = importlib.import_module("pyarrow.parquet")
    rows: list[dict[str, Any]] = pq.read_table(os.path.join(root, f"{name}.parquet")).to_pylist()
    return rows
