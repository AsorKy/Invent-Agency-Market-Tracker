"""Coordinate Brief reads, single-flight regeneration, and summarizer fallback.

``BriefService`` implements ARCH-0001 section E1.6: it derives the expected
``source_version`` from ``core``, serves a hit, a stale digest (queueing
regeneration), or regenerates synchronously on the first read. Regeneration
takes the Redis lock (``SET NX PX 30000``), tries the injected semantic
summarizer with a 5 second timeout and falls back to the rule-based one with
``degraded=True`` (REQ-BRF-001, REQ-BRF-002). It relates to ``main.py``,
``core_source.py``, ``rule_based.py``, ``single_flight.py`` and
``repository.py``.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from services.brief.app.core_source import CoreSource
from services.brief.app.models import BriefRules, CompanyFacts, StoredDigest
from services.brief.app.repository import BriefStore
from services.brief.app.rule_based import (
    RULE_SUMMARIZER_VERSION,
    RuleBasedSummarizer,
    Summarizer,
    build_rule_based_content,
)
from services.brief.app.single_flight import SingleFlightLock

logger = logging.getLogger(__name__)

SUMMARIZER_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class DigestResult:
    """A digest plus the stale flag used to build the response and header."""

    digest: StoredDigest
    stale: bool

    @property
    def cache_state(self) -> str:
        if self.stale:
            return "stale"
        return "degraded" if self.digest.degraded else "hit"


def _now_ms() -> int:
    return int(time.time() * 1000)


class BriefService:
    """Serve digests with stale-while-revalidate and single-flight regeneration."""

    def __init__(
        self,
        *,
        repository: BriefStore,
        lock: SingleFlightLock,
        core: CoreSource,
        summarizer: Summarizer | None = None,
        summarizer_timeout: float = SUMMARIZER_TIMEOUT_SECONDS,
        wait_interval: float = 0.05,
        wait_timeout: float = SUMMARIZER_TIMEOUT_SECONDS + 1.0,
    ) -> None:
        self.repository = repository
        self.lock = lock
        self.core = core
        self.summarizer = summarizer
        self.summarizer_timeout = summarizer_timeout
        self.wait_interval = wait_interval
        self.wait_timeout = wait_timeout

    async def load(
        self, company_id: UUID, tenant_id: UUID | None
    ) -> tuple[CompanyFacts, BriefRules, int]:
        """Read facts and rules from core; ``CoreUnavailable`` propagates."""
        facts = await self.core.get_facts(company_id, tenant_id)
        rules, config_version = await self.core.get_rules(tenant_id)
        return facts, rules, config_version

    @staticmethod
    def is_fresh(digest: StoredDigest, expected_version: int, config_version: int) -> bool:
        """A digest is fresh when its source and tenant config versions are current."""
        return digest.source_version == expected_version and digest.config_version >= config_version

    async def read(
        self, company_id: UUID, tenant_id: UUID | None
    ) -> tuple[DigestResult, "PendingRegeneration | None"]:
        """Resolve a digest; the second item is regeneration work to queue when stale."""
        facts, rules, config_version = await self.load(company_id, tenant_id)
        expected = facts.expected_version
        latest = await self.repository.get_latest(company_id, tenant_id)
        if latest is None:
            digest = await self.regenerate(
                facts,
                rules,
                config_version,
                blocking=True,
                expected_version=expected,
                tenant_id=tenant_id,
            )
            return DigestResult(digest, stale=False), None
        if self.is_fresh(latest, expected, config_version):
            return DigestResult(latest, stale=False), None
        pending = PendingRegeneration(self, facts, rules, config_version, expected, tenant_id)
        return DigestResult(latest, stale=True), pending

    async def regenerate(
        self,
        facts: CompanyFacts,
        rules: BriefRules,
        config_version: int,
        *,
        blocking: bool,
        expected_version: int,
        tenant_id: UUID | None = None,
    ) -> StoredDigest:
        """Regenerate under the Redis lock; losers wait (blocking) or return the latest."""
        company_id = facts.company_id
        key = f"brief:lock:{company_id}"
        owner = uuid4().hex
        if not await self.lock.acquire(key, owner):
            return await self._wait_for_winner(
                facts,
                rules,
                config_version,
                expected_version,
                blocking=blocking,
                tenant_id=tenant_id,
            )
        try:
            latest = await self.repository.get_latest(company_id, tenant_id)
            if latest is not None and self.is_fresh(latest, expected_version, config_version):
                return latest
            digest = await self._generate(facts, rules, config_version, expected_version)
            await self.repository.save(digest, tenant_id)
            return digest
        finally:
            await self.lock.release(key, owner)

    async def _wait_for_winner(
        self,
        facts: CompanyFacts,
        rules: BriefRules,
        config_version: int,
        expected_version: int,
        *,
        blocking: bool,
        tenant_id: UUID | None,
    ) -> StoredDigest:
        """Poll for the digest written by the lock holder, else serve an unsaved fallback."""
        deadline = time.monotonic() + (self.wait_timeout if blocking else 0.0)
        while True:
            latest = await self.repository.get_latest(facts.company_id, tenant_id)
            if latest is not None and (
                not blocking or self.is_fresh(latest, expected_version, config_version)
            ):
                return latest
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(self.wait_interval)
        return self._fallback(facts, rules, config_version, expected_version)

    async def _generate(
        self,
        facts: CompanyFacts,
        rules: BriefRules,
        config_version: int,
        expected_version: int,
    ) -> StoredDigest:
        """Run the summarizer with a timeout; any timeout or failure degrades to rules."""
        summarizer = self.summarizer or RuleBasedSummarizer()
        try:
            result = await asyncio.wait_for(
                summarizer.summarize(facts, rules), timeout=self.summarizer_timeout
            )
        except Exception as exc:  # any summarizer failure degrades to rules
            logger.warning("brief summarizer failed (%s); using rule fallback", type(exc).__name__)
            return self._fallback(facts, rules, config_version, expected_version)
        return StoredDigest(
            company_id=facts.company_id,
            source_version=expected_version,
            content=result.content,
            generated_at=_now_ms(),
            summarizer_version=result.version,
            degraded=False,
            config_version=config_version,
        )

    @staticmethod
    def _fallback(
        facts: CompanyFacts, rules: BriefRules, config_version: int, expected_version: int
    ) -> StoredDigest:
        return StoredDigest(
            company_id=facts.company_id,
            source_version=expected_version,
            content=build_rule_based_content(facts, rules),
            generated_at=_now_ms(),
            summarizer_version=RULE_SUMMARIZER_VERSION,
            degraded=True,
            config_version=config_version,
        )


class PendingRegeneration:
    """Deferred stale-while-revalidate work, run by a background task."""

    def __init__(
        self,
        service: BriefService,
        facts: CompanyFacts,
        rules: BriefRules,
        config_version: int,
        expected_version: int,
        tenant_id: UUID | None = None,
    ) -> None:
        self._args = (service, facts, rules, config_version, expected_version, tenant_id)

    async def run(self) -> None:
        service, facts, rules, config_version, expected_version, tenant_id = self._args
        try:
            await service.regenerate(
                facts,
                rules,
                config_version,
                blocking=False,
                expected_version=expected_version,
                tenant_id=tenant_id,
            )
        except Exception:  # a background failure must not crash the worker
            logger.exception("brief background regeneration failed")
