"""Provide canonical UTC and Colombia-local time conversions.

The helpers relate to Pulse rollups, API contracts, and event serialization.
They keep instants in UTC and convert reporting days through an explicit IANA
timezone so services do not mix local dates with timestamp values.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_CITY_TIMEZONE = "America/Bogota"
CITY_TIMEZONES = {
    "bogota": "America/Bogota",
    "barranquilla": "America/Bogota",
    "cali": "America/Bogota",
    "cartagena": "America/Bogota",
    "medellin": "America/Bogota",
}


def utc_epoch_ms(value: datetime) -> int:
    """Convert an aware datetime to UTC epoch milliseconds."""

    if value.tzinfo is None:
        raise ValueError("timestamp must include timezone information")
    return int(value.astimezone(UTC).timestamp() * 1000)


def local_day(value: datetime, city: str | None = None) -> date:
    """Return the reporting date in the configured city's IANA timezone."""

    if value.tzinfo is None:
        raise ValueError("timestamp must include timezone information")
    timezone_name = CITY_TIMEZONES.get((city or "").strip().lower(), DEFAULT_CITY_TIMEZONE)
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone_name}") from exc
    return value.astimezone(timezone).date()
