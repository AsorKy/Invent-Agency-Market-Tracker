"""Define the Pulse response rules shared by the service and its consumers.

The module holds only constants and pure functions so it adds no Pydantic model
to the frozen contract snapshot. It relates to ``services/pulse/app/main.py`` and
``services/pulse/app/models.py`` and encodes ADR-IMT-0001 section E1.5: the
26-hour stale rule, the low-sample threshold, and the browser cache header.
"""

STALE_AFTER_MS = 26 * 60 * 60 * 1000
LOW_SAMPLE_BELOW = 5
CACHE_CONTROL = "private, max-age=60"


def is_stale(as_of_ms: int, now_ms: int) -> bool:
    """Return True when ``as_of_ms`` is older than 26 hours at ``now_ms``."""
    return now_ms - as_of_ms > STALE_AFTER_MS


def is_low_sample(n: int) -> bool:
    """Return True when a group has fewer than five observations."""
    return n < LOW_SAMPLE_BELOW
