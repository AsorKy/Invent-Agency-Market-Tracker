"""Unit tests for the enrichment agent contract (REQ-ING-006).

Uses the mock agent and the in-memory store; no network or database.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from services.ingest.app.enrichment import (  # noqa: E402
    BRIEF_WARNING_CODE,
    DailyBudget,
    EnrichmentService,
    EnrichmentStatus,
    InMemoryEnrichmentStore,
    MockEnrichmentAgent,
    SlidingWindowRateLimiter,
    brief_warning,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _service(agent: MockEnrichmentAgent, **kwargs: object) -> EnrichmentService:
    return EnrichmentService(agent, InMemoryEnrichmentStore(), **kwargs)  # type: ignore[arg-type]


def test_success_marks_done_without_warning() -> None:
    service = _service(MockEnrichmentAgent())
    service.request("t1", "c1", "https://example.com")
    [done] = asyncio.run(service.run_due(NOW))
    assert done.status is EnrichmentStatus.DONE
    assert done.result and done.result["provider"] == "mock"
    assert brief_warning(done) is None


def test_request_is_idempotent() -> None:
    service = _service(MockEnrichmentAgent())
    assert service.request("t1", "c1") is service.request("t1", "c1")


def test_failure_sets_pending_retry_with_backoff_and_warning() -> None:
    service = _service(MockEnrichmentAgent(fail_times=1))
    service.request("t1", "c1")
    [failed] = asyncio.run(service.run_due(NOW))
    assert failed.status is EnrichmentStatus.PENDING_RETRY
    assert failed.next_attempt_at == NOW + timedelta(seconds=60)
    warning = brief_warning(failed)
    assert warning is not None
    assert warning["code"] == BRIEF_WARNING_CODE and warning["severity"] == "warning"
    # Not due before the backoff elapses, then succeeds.
    assert asyncio.run(service.run_due(NOW + timedelta(seconds=30))) == []
    [done] = asyncio.run(service.run_due(NOW + timedelta(seconds=61)))
    assert done.status is EnrichmentStatus.DONE


def test_repeated_failures_dead_letter() -> None:
    service = _service(MockEnrichmentAgent(fail_times=99), max_failures=2)
    service.request("t1", "c1")
    asyncio.run(service.run_due(NOW))
    [last] = asyncio.run(service.run_due(NOW + timedelta(hours=1)))
    assert last.status is EnrichmentStatus.DEAD_LETTER
    assert brief_warning(last) is None


def test_daily_budget_defers_until_next_day() -> None:
    agent = MockEnrichmentAgent()
    service = _service(agent, budget=DailyBudget(1))
    service.request("t1", "c1")
    service.request("t1", "c2")
    assert len(asyncio.run(service.run_due(NOW))) == 1
    assert asyncio.run(service.run_due(NOW)) == []
    assert len(asyncio.run(service.run_due(NOW + timedelta(days=1)))) == 1
    assert agent.calls == 2


def test_rate_limit_defers_excess_calls() -> None:
    service = _service(MockEnrichmentAgent(), limiter=SlidingWindowRateLimiter(1, 60))
    service.request("t1", "c1")
    service.request("t1", "c2")
    assert len(asyncio.run(service.run_due(NOW))) == 1
    assert len(asyncio.run(service.run_due(NOW + timedelta(seconds=61)))) == 1


def test_invalid_limits_rejected() -> None:
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(0, 60)
    with pytest.raises(ValueError):
        DailyBudget(-1)
