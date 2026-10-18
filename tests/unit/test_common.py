"""Unit tests for the shared helpers in ``libs/common``.

Covers keyset cursors (REQ-QA-002), bounded retries, UTC millisecond helpers
(REQ-DAT-002), and log redaction (REQ-SEC-001). The tests use only the standard
library: sleeps are patched out and jitter is controlled, so they never wait.
"""

import asyncio
import base64
import json
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from libs.common import retry as retry_module
from libs.common.log_redaction import (
    REDACTED,
    redact_headers,
    redact_log_record,
    redact_text,
)
from libs.common.pagination import PageCursor, decode_cursor, encode_cursor
from libs.common.retry import retry_with_backoff
from libs.common.time import local_day, utc_epoch_ms

BOGOTA = timezone(timedelta(hours=-5))


# --- pagination -------------------------------------------------------------


def test_cursor_round_trip_preserves_values() -> None:
    stamp = datetime(2026, 3, 4, 5, 6, 7, 123456, tzinfo=UTC)
    item_id = uuid4()
    cursor = decode_cursor(encode_cursor(stamp, item_id))
    assert cursor == PageCursor(updated_at=stamp, item_id=item_id)


def test_cursor_is_url_safe_and_unpadded() -> None:
    value = encode_cursor(datetime(2026, 1, 1, tzinfo=BOGOTA), uuid4())
    assert "=" not in value
    assert all(c.isalnum() or c in "-_" for c in value)


def test_cursor_keeps_offset_and_orders_like_keyset() -> None:
    base = datetime(2026, 1, 1, 12, tzinfo=UTC)
    rows = [
        (base + timedelta(seconds=2), UUID(int=1)),
        (base, UUID(int=9)),
        (base, UUID(int=3)),
        (base + timedelta(seconds=1), UUID(int=5)),
    ]
    decoded = [decode_cursor(encode_cursor(ts, rid)) for ts, rid in rows]
    ordered = sorted(decoded, key=lambda c: (c.updated_at, c.item_id))
    assert [(c.updated_at, c.item_id) for c in ordered] == sorted(rows)
    local = decode_cursor(encode_cursor(datetime(2026, 1, 1, 7, tzinfo=BOGOTA), uuid4()))
    assert local.updated_at == datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert local.updated_at.utcoffset() == timedelta(hours=-5)


def _raw_cursor(payload: object) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "!!!not-base64!!!",
        base64.urlsafe_b64encode(b"not json").decode("ascii"),
        base64.urlsafe_b64encode(b"\xff\xfe").decode("ascii"),
        _raw_cursor([1, 2]),
        _raw_cursor({"updated_at": "2026-01-01T00:00:00+00:00"}),
        _raw_cursor({"updated_at": "garbage", "item_id": str(uuid4())}),
        _raw_cursor({"updated_at": "2026-01-01T00:00:00+00:00", "item_id": "nope"}),
        _raw_cursor({"updated_at": "2026-01-01T00:00:00", "item_id": str(uuid4())}),
    ],
)
def test_decode_cursor_rejects_malformed_input(value: str) -> None:
    with pytest.raises(ValueError, match="invalid_cursor"):
        decode_cursor(value)


# --- retry ------------------------------------------------------------------


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(retry_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(retry_module.random, "uniform", lambda low, high: high)
    return recorded


class Flaky:
    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.calls = 0
        self.failures = failures
        self.error = error or ConnectionError("boom")

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


def test_retry_returns_first_success_without_sleeping(sleeps: list[float]) -> None:
    op = Flaky(0)
    assert asyncio.run(retry_with_backoff(op)) == "ok"
    assert op.calls == 1
    assert sleeps == []


def test_retry_recovers_after_transient_failures(sleeps: list[float]) -> None:
    op = Flaky(2)
    assert asyncio.run(retry_with_backoff(op, attempts=3)) == "ok"
    assert op.calls == 3
    assert len(sleeps) == 2


def test_retry_raises_last_error_after_exhausting_attempts(sleeps: list[float]) -> None:
    op = Flaky(10)
    with pytest.raises(ConnectionError):
        asyncio.run(retry_with_backoff(op, attempts=4))
    assert op.calls == 4
    assert len(sleeps) == 3


def test_retry_backoff_is_exponential_jittered_and_capped(sleeps: list[float]) -> None:
    op = Flaky(10)
    with pytest.raises(ConnectionError):
        asyncio.run(
            retry_with_backoff(
                op,
                attempts=6,
                base_delay_seconds=0.1,
                max_delay_seconds=0.5,
                jitter_ratio=0.2,
            )
        )
    # uniform is patched to the upper bound, so delay = base * 2**n * 1.2, capped.
    assert sleeps == pytest.approx([0.12, 0.24, 0.48, 0.5, 0.5])
    assert max(sleeps) <= 0.5


def test_retry_without_jitter_is_deterministic(sleeps: list[float]) -> None:
    op = Flaky(10)
    with pytest.raises(ConnectionError):
        asyncio.run(retry_with_backoff(op, attempts=3, base_delay_seconds=1, jitter_ratio=0))
    assert sleeps == [1, 2]


def test_retry_does_not_retry_unlisted_exceptions(sleeps: list[float]) -> None:
    op = Flaky(5, ValueError("bad input"))
    with pytest.raises(ValueError):
        asyncio.run(retry_with_backoff(op, attempts=5, retry_on=(ConnectionError,)))
    assert op.calls == 1
    assert sleeps == []


def test_retry_single_attempt_never_sleeps(sleeps: list[float]) -> None:
    op = Flaky(1)
    with pytest.raises(ConnectionError):
        asyncio.run(retry_with_backoff(op, attempts=1))
    assert op.calls == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attempts": 0},
        {"base_delay_seconds": -1},
        {"max_delay_seconds": -0.1},
        {"jitter_ratio": -0.1},
        {"jitter_ratio": 1.1},
    ],
)
def test_retry_rejects_invalid_configuration(kwargs: dict[str, float]) -> None:
    op = Flaky(0)
    with pytest.raises(ValueError):
        asyncio.run(retry_with_backoff(op, **kwargs))  # type: ignore[arg-type]
    assert op.calls == 0


# --- time -------------------------------------------------------------------


def test_utc_epoch_ms_epoch_and_milliseconds() -> None:
    assert utc_epoch_ms(datetime(1970, 1, 1, tzinfo=UTC)) == 0
    assert utc_epoch_ms(datetime(1970, 1, 1, 0, 0, 1, 500000, tzinfo=UTC)) == 1500


def test_utc_epoch_ms_is_offset_independent() -> None:
    utc_value = datetime(2026, 5, 1, 17, 0, tzinfo=UTC)
    local_value = datetime(2026, 5, 1, 12, 0, tzinfo=BOGOTA)
    assert utc_epoch_ms(local_value) == utc_epoch_ms(utc_value) == 1777654800000


def test_utc_epoch_ms_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone"):
        utc_epoch_ms(datetime(2026, 1, 1))


def test_local_day_uses_bogota_calendar_day() -> None:
    late_utc = datetime(2026, 1, 2, 3, 30, tzinfo=UTC)  # 22:30 on Jan 1 in Bogota
    assert local_day(late_utc) == date(2026, 1, 1)
    assert local_day(late_utc, "  Medellin ") == date(2026, 1, 1)
    assert local_day(late_utc, "unknown-city") == date(2026, 1, 1)
    assert local_day(datetime(2026, 1, 2, 5, 0, tzinfo=UTC), "cali") == date(2026, 1, 2)


def test_local_day_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone"):
        local_day(datetime(2026, 1, 1))


# --- log redaction ----------------------------------------------------------


def test_redact_text_masks_document_numbers_keeping_check_digit() -> None:
    text = "NIT 900123456-7 and 12345678-9 registered"
    assert redact_text(text, allowed_email_domains=frozenset()) == (
        "NIT ***-***-***-7 and ***-***-***-9 registered"
    )
    assert "900123456" not in redact_text(text, allowed_email_domains=frozenset())


def test_redact_text_leaves_short_numbers_alone() -> None:
    assert redact_text("order 12345-6", allowed_email_domains=frozenset()) == "order 12345-6"


def test_redact_text_masks_external_emails_and_keeps_allowlisted() -> None:
    text = "contact jane.doe@Example.com or ops@agency.co"
    result = redact_text(text, allowed_email_domains=frozenset({"agency.co"}))
    assert "jane.doe" not in result
    assert "c***@example.com" in result
    assert "ops@agency.co" in result


def test_redact_text_reads_allowlist_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_ALLOWED_EMAIL_DOMAINS", " @Agency.co , other.org ")
    assert (
        redact_text("a@agency.co b@other.org c@evil.com") == "a@agency.co b@other.org c***@evil.com"
    )
    monkeypatch.delenv("LOG_ALLOWED_EMAIL_DOMAINS")
    assert redact_text("a@agency.co") == "c***@agency.co"


def test_redact_headers_hides_secret_headers_case_insensitively() -> None:
    headers = {
        "Authorization": "Bearer abc",
        "COOKIE": "sid=1",
        "Set-Cookie": "sid=1",
        "Proxy-Authorization": "Basic x",
        "X-CSRF-Token": "csrf",
        "x-edge-secret": "edge",
        "Accept": "application/json",
        "X-Contact": "owner@example.com",
    }
    result = redact_headers(headers)
    for name in (
        "Authorization",
        "COOKIE",
        "Set-Cookie",
        "Proxy-Authorization",
        "X-CSRF-Token",
        "x-edge-secret",
    ):
        assert result[name] == REDACTED
    assert result["Accept"] == "application/json"
    assert result["X-Contact"] == "c***@example.com"


def test_redact_log_record_masks_secret_bearing_keys() -> None:
    event = {
        "event": "request failed for 900123456-7",
        "Token": "abc",
        "secret": "s3cr3t",
        "payload": {"nit": "900123456-7"},
        "request_body": "x",
        "response_body": "y",
        "cookie": "sid",
        "authorization": "Bearer z",
        "x-edge-secret": "edge",
        "status": 500,
        "note": "mail bob@example.com",
    }
    result = redact_log_record(None, "info", event)
    for key in (
        "Token",
        "secret",
        "payload",
        "request_body",
        "response_body",
        "cookie",
        "authorization",
        "x-edge-secret",
    ):
        assert result[key] == REDACTED
    assert result["event"] == "request failed for ***-***-***-7"
    assert result["note"] == "mail c***@example.com"
    assert result["status"] == 500


def test_redact_log_record_redacts_headers_mapping_per_header() -> None:
    event = {"headers": {"Authorization": "Bearer abc", "Accept": "text/html"}}
    result = redact_log_record(None, "info", event)
    assert result["headers"] == {"Authorization": REDACTED, "Accept": "text/html"}


def test_redact_log_record_redacts_non_mapping_headers_entirely() -> None:
    result = redact_log_record(None, "info", {"headers": "Authorization: Bearer abc"})
    assert result["headers"] == REDACTED
