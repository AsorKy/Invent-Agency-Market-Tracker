"""Test the pure Streamlit helpers without a running server (REQ-STR-001, REQ-STR-002)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

from services.streamlit.app import helpers

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _fetch(status: int, headers: dict[str, str], body: Any) -> helpers.FetchFn:
    def run(_url: str, _timeout: float) -> tuple[int, dict[str, str], Any]:
        return status, headers, body

    return run


def test_safe_fetch_ok_with_header_as_of() -> None:
    fetch = _fetch(200, {"x-as-of": "2026-01-01T11:55:00Z"}, {"rows": [1]})
    data, error, as_of, stale = helpers.safe_fetch("http://x/a", fetch=fetch, now=NOW)
    assert data == {"rows": [1]} and error is None
    assert as_of == datetime(2026, 1, 1, 11, 55, tzinfo=UTC)
    assert stale is False
    assert helpers.format_as_of(as_of) == "Data as of 2026-01-01 06:55 COT"


def test_safe_fetch_stale_by_age_and_by_flag() -> None:
    old = _fetch(200, {"X-As-Of": "2026-01-01T10:00:00+00:00"}, {})
    assert helpers.safe_fetch("u", fetch=old, now=NOW).stale is True
    flagged = _fetch(200, {}, {"stale": True, "as_of": "2026-01-01T11:59:00Z"})
    result = helpers.safe_fetch("u", fetch=flagged, now=NOW)
    assert result.stale is True and result.as_of is not None


def test_safe_fetch_errors_never_raise_or_leak() -> None:
    assert helpers.safe_fetch("u", fetch=_fetch(503, {}, None)).error == helpers.GENERIC_ERROR

    def boom(_url: str, _timeout: float) -> tuple[int, dict[str, str], Any]:
        raise httpx.ConnectError("secret-host:5432")

    result = helpers.safe_fetch("u", fetch=boom)
    assert result.data is None and result.error == helpers.GENERIC_ERROR
    assert "secret" not in (result.error or "")


def test_parse_as_of_invalid_and_naive() -> None:
    assert helpers.parse_as_of("nope") is None
    assert helpers.parse_as_of(None) is None
    assert helpers.parse_as_of("2026-01-01T00:00:00") == datetime(2026, 1, 1, tzinfo=UTC)
    assert helpers.format_as_of(None) == "Data time unknown"
    assert helpers.is_stale(None) is False
    assert helpers.is_stale(NOW - timedelta(minutes=1), now=NOW) is False


@pytest.mark.parametrize(
    ("role", "export"),
    [
        ("admin", True),
        ("analyst", True),
        ("strategist", False),
        ("bd_rep", False),
        ("intake", False),
        ("root", False),
        (None, False),
    ],
)
def test_export_visibility(role: str | None, export: bool) -> None:
    assert helpers.can_export({"role": role}) is export


def test_role_gating() -> None:
    assert helpers.has_access({"role": "bd_rep"}) is True
    assert helpers.has_access({}) is False
    assert helpers.has_access({"role": "intake"}, ["admin"]) is False
    assert helpers.has_access({"role": "admin"}, ["admin"]) is True


def test_filters_round_trip_and_invalid_input() -> None:
    filters = helpers.filters_from_params(
        {"city": "bogota", "line": "web", "from": "2026-01-01", "to": "2026-02-01"}
    )
    assert filters.date_from == date(2026, 1, 1)
    assert helpers.filters_from_params(helpers.filters_to_params(filters)) == filters
    assert helpers.filters_from_params({"from": "x"}).date_from is None
    swapped = helpers.filters_from_params({"from": "2026-03-01", "to": "2026-01-01"})
    assert swapped.date_from is None and swapped.date_to is None
    assert helpers.filters_to_params(helpers.Filters()) == {}


def test_sync_filters_state_wins_and_updates_url() -> None:
    state: dict[str, Any] = {}
    params = {"city": "cali", "stale": "keep"}
    assert helpers.sync_filters(state, params).city == "cali"
    state["filters"] = helpers.Filters(city="all", service_line="seo")
    helpers.sync_filters(state, params)
    assert params == {"stale": "keep", "line": "seo"}


def test_gateway_url_and_parquet_guard(tmp_path: Any) -> None:
    assert helpers.gateway_url("/api/v1/x", {"GATEWAY_URL": "http://g:1/"}) == "http://g:1/api/v1/x"
    assert helpers.gateway_url("a", {}) == "http://gateway:8000/a"
    with pytest.raises(ValueError):
        helpers.read_parquet("../etc", str(tmp_path))
    pq = pytest.importorskip("pyarrow.parquet")
    pa = pytest.importorskip("pyarrow")
    pq.write_table(pa.table({"a": [1, 2]}), str(tmp_path / "t.parquet"))
    assert helpers.read_parquet("t", str(tmp_path)) == [{"a": 1}, {"a": 2}]
