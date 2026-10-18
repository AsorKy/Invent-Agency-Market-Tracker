"""Deep-enrichment agent contract for ingest (REQ-ING-006).

A deep-enrichment request is queued, rate limited, and counted against a daily
budget before it reaches a replaceable ``EnrichmentAgent``. Run 1 ships only
``MockEnrichmentAgent``; a Bright Data MCP agent can replace it without
touching the service. A provider failure moves the request to
``pending_retry`` with exponential backoff (reusing ``budget.py``) and is
exposed to the Brief as a ``warning`` alert, never as an HTTP 500.

Persistence is behind the ``EnrichmentStore`` protocol; an in-memory store is
provided. No ``ingest`` table exists for enrichment yet, so a SQL store needs a
migration owned by the database engineer. The module does no network I/O.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from services.ingest.app.budget import MAX_FAILURES, next_failure_state

BRIEF_WARNING_CODE = "enrichment_pending_retry"
BRIEF_WARNING_SEVERITY = "warning"
DEFAULT_DAILY_BUDGET = 50
DEFAULT_RATE_LIMIT = 5
DEFAULT_RATE_WINDOW_SECONDS = 60


class EnrichmentStatus(StrEnum):
    """Lifecycle states of an enrichment request."""

    QUEUED = "queued"
    PENDING_RETRY = "pending_retry"
    DONE = "done"
    DEAD_LETTER = "dead_letter"


class EnrichmentProviderError(Exception):
    """Raised by an agent when the provider fails; the request is retried."""


@dataclass(frozen=True)
class EnrichmentRequest:
    """One deep-enrichment request for a company of a tenant."""

    tenant_id: str
    company_id: str
    website: str | None = None
    status: EnrichmentStatus = EnrichmentStatus.QUEUED
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    result: dict[str, Any] | None = None


class EnrichmentAgent(Protocol):
    """Replaceable provider interface; implementations must be idempotent."""

    async def enrich(self, request: EnrichmentRequest) -> dict[str, Any]:
        """Return enrichment data or raise ``EnrichmentProviderError``."""
        ...


@dataclass
class MockEnrichmentAgent:
    """Deterministic agent for run 1; fails the first ``fail_times`` calls."""

    fail_times: int = 0
    calls: int = field(default=0, init=False)

    async def enrich(self, request: EnrichmentRequest) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise EnrichmentProviderError("mock provider unavailable")
        return {
            "provider": "mock",
            "company_id": request.company_id,
            "website": request.website,
            "stack": [],
            "contact_forms": [],
        }


class EnrichmentStore(Protocol):
    """Storage seam for requests keyed by (tenant_id, company_id)."""

    def get(self, tenant_id: str, company_id: str) -> EnrichmentRequest | None: ...

    def put(self, request: EnrichmentRequest) -> None: ...

    def due(self, now: datetime) -> list[EnrichmentRequest]: ...


class InMemoryEnrichmentStore:
    """Dictionary store used by tests and run 1."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], EnrichmentRequest] = {}

    def get(self, tenant_id: str, company_id: str) -> EnrichmentRequest | None:
        return self._items.get((tenant_id, company_id))

    def put(self, request: EnrichmentRequest) -> None:
        self._items[(request.tenant_id, request.company_id)] = request

    def due(self, now: datetime) -> list[EnrichmentRequest]:
        """Return queued requests and retries whose backoff elapsed."""
        return [
            item
            for item in self._items.values()
            if item.status is EnrichmentStatus.QUEUED
            or (
                item.status is EnrichmentStatus.PENDING_RETRY
                and item.next_attempt_at is not None
                and item.next_attempt_at <= now
            )
        ]


class SlidingWindowRateLimiter:
    """Allow at most ``limit`` calls per ``window_seconds``."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("limit and window must be positive")
        self.limit = limit
        self.window = timedelta(seconds=window_seconds)
        self._hits: list[datetime] = []

    def allow(self, now: datetime) -> bool:
        """Record a call and return True when it is within the limit."""
        self._hits = [hit for hit in self._hits if now - hit < self.window]
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True


class DailyBudget:
    """Count provider calls per tenant and UTC day."""

    def __init__(self, max_per_day: int) -> None:
        if max_per_day < 0:
            raise ValueError("daily budget must not be negative")
        self.max_per_day = max_per_day
        self._used: dict[tuple[str, date], int] = {}

    def consume(self, tenant_id: str, now: datetime) -> bool:
        """Reserve one call; return False when the day is exhausted."""
        key = (tenant_id, now.date())
        used = self._used.get(key, 0)
        if used >= self.max_per_day:
            return False
        self._used[key] = used + 1
        return True


def brief_warning(request: EnrichmentRequest) -> dict[str, str] | None:
    """Return a Brief alert (code, severity, message) for a retrying request."""
    if request.status is not EnrichmentStatus.PENDING_RETRY:
        return None
    return {
        "code": BRIEF_WARNING_CODE,
        "severity": BRIEF_WARNING_SEVERITY,
        "message": "Deep enrichment is delayed by a provider failure and will be retried.",
    }


class EnrichmentService:
    """Queue requests and run them through the agent with limits and backoff."""

    def __init__(
        self,
        agent: EnrichmentAgent,
        store: EnrichmentStore,
        *,
        limiter: SlidingWindowRateLimiter | None = None,
        budget: DailyBudget | None = None,
        max_failures: int = MAX_FAILURES,
        backoff_base_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.agent = agent
        self.store = store
        self.limiter = limiter or SlidingWindowRateLimiter(
            DEFAULT_RATE_LIMIT, DEFAULT_RATE_WINDOW_SECONDS
        )
        self.budget = budget or DailyBudget(DEFAULT_DAILY_BUDGET)
        self.max_failures = max_failures
        self.backoff_base_seconds = backoff_base_seconds
        self.clock = clock

    def request(
        self, tenant_id: str, company_id: str, website: str | None = None
    ) -> EnrichmentRequest:
        """Queue a request; an existing one for the company is returned as is."""
        existing = self.store.get(tenant_id, company_id)
        if existing is not None and existing.status is not EnrichmentStatus.DEAD_LETTER:
            return existing
        item = EnrichmentRequest(tenant_id, company_id, website)
        self.store.put(item)
        return item

    async def run_due(self, now: datetime) -> list[EnrichmentRequest]:
        """Process due requests; rate limit or budget exhaustion defers the rest."""
        processed: list[EnrichmentRequest] = []
        for item in self.store.due(now):
            if not self.budget.consume(item.tenant_id, now):
                continue
            if not self.limiter.allow(now):
                break
            processed.append(await self._attempt(item, now))
        return processed

    async def _attempt(self, item: EnrichmentRequest, now: datetime) -> EnrichmentRequest:
        try:
            data = await self.agent.enrich(item)
        except EnrichmentProviderError as exc:
            state = next_failure_state(
                item.attempts,
                now,
                max_failures=self.max_failures,
                base_seconds=self.backoff_base_seconds,
            )
            updated = replace(
                item,
                status=(
                    EnrichmentStatus.DEAD_LETTER
                    if state.dead_lettered_at
                    else EnrichmentStatus.PENDING_RETRY
                ),
                attempts=state.attempts,
                next_attempt_at=state.next_attempt_at,
                last_error=str(exc)[:500],
            )
        else:
            updated = replace(
                item,
                status=EnrichmentStatus.DONE,
                next_attempt_at=None,
                last_error=None,
                result=data,
            )
        self.store.put(updated)
        return updated
