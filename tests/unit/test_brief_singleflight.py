"""Unit tests for Brief single-flight and summarizer fallback (REQ-BRF-002)."""

import asyncio

import pytest

pytest.importorskip("pydantic")
from services.brief.app.models import BriefRules, CompanyFacts  # noqa: E402
from services.brief.app.repository import BriefRepository  # noqa: E402
from services.brief.app.rule_based import SummaryResult, build_rule_based_content  # noqa: E402
from services.brief.app.service import BriefService  # noqa: E402
from services.brief.app.single_flight import SingleFlightLock  # noqa: E402
from tests.unit.support_brief import COMPANY, FakeCore, FakeRedis  # noqa: E402


class CountingSummarizer:
    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.calls = 0
        self.gate = gate

    async def summarize(self, facts: CompanyFacts, rules: BriefRules) -> SummaryResult:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return SummaryResult(build_rule_based_content(facts, rules), "llm-v1")


class SlowSummarizer:
    async def summarize(self, facts: CompanyFacts, rules: BriefRules) -> SummaryResult:
        await asyncio.sleep(10)
        raise AssertionError("must be cancelled by the timeout")


class FailingSummarizer:
    async def summarize(self, facts: CompanyFacts, rules: BriefRules) -> SummaryResult:
        raise RuntimeError("model down")


def _service(
    summarizer: object | None, redis: FakeRedis | None = None, **kw: float
) -> BriefService:
    return BriefService(
        repository=BriefRepository(None),
        lock=SingleFlightLock(redis or FakeRedis(), ttl_ms=30_000),  # type: ignore[arg-type]
        core=FakeCore(),
        summarizer=summarizer,  # type: ignore[arg-type]
        **kw,
    )


async def test_concurrent_first_reads_run_one_summarization() -> None:
    gate = asyncio.Event()
    summarizer = CountingSummarizer(gate)
    service = _service(summarizer, wait_interval=0.001)
    readers = [asyncio.create_task(service.read(COMPANY, None)) for _ in range(5)]
    await asyncio.sleep(0.05)
    gate.set()
    results = await asyncio.gather(*readers)
    assert summarizer.calls == 1
    assert {r[0].digest.generated_at for r in results} == {results[0][0].digest.generated_at}
    assert all(not r[0].digest.degraded for r in results)


async def test_lock_uses_nx_px_and_owner_only_release() -> None:
    redis = FakeRedis()
    service = _service(None, redis)
    await service.read(COMPANY, None)
    assert redis.set_calls == [(f"brief:lock:{COMPANY}", 30_000)]
    assert redis.locks == {}
    lock = SingleFlightLock(redis, ttl_ms=30_000)  # type: ignore[arg-type]
    assert await lock.acquire("k", "a")
    assert not await lock.acquire("k", "b")
    await lock.release("k", "b")
    assert redis.locks["k"] == "a"
    await lock.release("k", "a")
    assert "k" not in redis.locks


async def test_timeout_falls_back_to_rules_degraded() -> None:
    service = _service(SlowSummarizer(), summarizer_timeout=0.01)
    result, _ = await service.read(COMPANY, None)
    assert result.digest.degraded is True
    assert result.digest.summarizer_version == "rule-v3"
    assert result.cache_state == "degraded"


async def test_summarizer_failure_falls_back_to_rules_degraded() -> None:
    service = _service(FailingSummarizer())
    result, _ = await service.read(COMPANY, None)
    assert result.digest.degraded is True
    saved = await service.repository.get_latest(COMPANY)
    assert saved is not None and saved.degraded is True


async def test_default_rule_summarizer_is_not_degraded() -> None:
    result, _ = await _service(None).read(COMPANY, None)
    assert result.digest.degraded is False
    assert result.digest.summarizer_version == "rule-v3"


async def test_background_regeneration_skips_when_lock_is_held() -> None:
    redis = FakeRedis()
    summarizer = CountingSummarizer()
    service = _service(summarizer, redis)
    await service.read(COMPANY, None)
    service.core.facts = CompanyFacts(company_id=COMPANY, version=5)  # type: ignore[attr-defined]
    redis.locks[f"brief:lock:{COMPANY}"] = "other-worker"
    result, pending = await service.read(COMPANY, None)
    assert result.stale and pending is not None
    await pending.run()
    assert summarizer.calls == 1
