"""Score and brief the demo companies after the seed load (REQ-RUN-001, T-141).

Core's outbox, the Pulse consumer, and the ML consumer only react to events: the ML consumer
marks scores stale and never creates one, and Brief builds a digest only when somebody reads it.
Without a caller the demo therefore left ``ml.lead_score`` and ``brief.record_digest`` empty. This
step is that caller. For every demo company it derives bounded score features from what the load
delivered, posts them to ML (which stores the score and publishes ``lead.score_updated`` for core's
auto-qualification), then reads the Brief digest (rule-based fallback, no LLM key needed), which
persists the row. Calls use signed service tokens and send the demo tenant in ``X-Tenant-Id``.
``core.brief`` stays empty on purpose: it is keyed by project, not by company. It relates to
``ops/seed/loader.py``, ``ops/seed/__main__.py``, ``services/ml/app/main.py`` and
``services/brief/app/main.py``.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

import httpx

from libs.common.internal_auth import service_token_header
from libs.domain.score_features import (
    SCORE_SOURCE_VERSION,
    SIGNALS_FOR_FULL_STRENGTH,
    CompanyProfile,
    score_features,
)

__all__ = ["SCORE_SOURCE_VERSION", "SIGNALS_FOR_FULL_STRENGTH", "CompanyProfile", "score_features"]

MAX_ATTEMPTS = 5


class ScoreBriefPort(Protocol):
    """The two internal calls the enrichment needs."""

    async def score(self, features: dict[str, Any]) -> dict[str, Any]: ...

    async def brief(self, company_id: str) -> dict[str, Any]: ...


@dataclass
class EnrichResult:
    """Counts of one enrichment run."""

    scored: int = 0
    briefed: int = 0
    failed: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


class TrackingCore:
    """``SeedCore`` decorator that records a profile per company while forwarding calls."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.profiles: dict[str, CompanyProfile] = {}

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await self.inner.upsert_company(body)
        profile = self.profiles.setdefault(str(result["company_id"]), CompanyProfile())
        profile.city = profile.city or body.get("city")
        profile.has_website = profile.has_website or bool(body.get("website"))
        profile.has_nit = profile.has_nit or bool(body.get("nit"))
        return result

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await self.inner.create_signal(body)
        self.profiles.setdefault(str(body["company_id"]), CompanyProfile()).signals += 1
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class HttpScoreBrief:
    """Real ``ScoreBriefPort`` over the ML and Brief internal APIs with a signed service token."""

    service_name = "seed"

    def __init__(
        self,
        ml_url: str,
        brief_url: str,
        service_token: str,
        tenant_id: UUID,
        *,
        timeout_seconds: float = 30.0,
        ml_client: httpx.AsyncClient | None = None,
        brief_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._ml = ml_client or httpx.AsyncClient(
            base_url=ml_url.rstrip("/"), timeout=timeout_seconds
        )
        self._brief = brief_client or httpx.AsyncClient(
            base_url=brief_url.rstrip("/"), timeout=timeout_seconds
        )
        self._secret = service_token
        self._tenant = str(tenant_id)

    def _headers(self) -> dict[str, str]:
        return service_token_header(self.service_name, self._secret) | {"X-Tenant-Id": self._tenant}

    async def score(self, features: dict[str, Any]) -> dict[str, Any]:
        response = await self._ml.post(
            "/internal/ml/scores", json=features, headers=self._headers()
        )
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    async def brief(self, company_id: str) -> dict[str, Any]:
        response = await self._brief.get(
            f"/internal/brief/companies/{company_id}/digest", headers=self._headers()
        )
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    async def aclose(self) -> None:
        await self._ml.aclose()
        await self._brief.aclose()


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


async def _attempt(call: Any, arg: Any, sleep: Any, base_delay: float) -> dict[str, Any]:
    """Run one call with a bounded retry on transport errors and 5xx (services may be starting)."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            result: dict[str, Any] = await call(arg)
            return result
        except Exception as exc:
            if attempt == MAX_ATTEMPTS - 1 or not _retryable(exc):
                raise
            await sleep(base_delay * 2**attempt)
    raise AssertionError("unreachable")


async def enrich(
    port: ScoreBriefPort,
    profiles: dict[str, CompanyProfile],
    *,
    sleep: Any,
    base_delay: float = 1.0,
) -> EnrichResult:
    """Score then brief every profiled company; one failure never stops the others."""
    result = EnrichResult()
    for features in score_features(profiles):
        company_id = str(features["company_id"])
        step = "score"
        try:
            await _attempt(port.score, features, sleep, base_delay)
            result.scored += 1
            step = "brief"
            await _attempt(port.brief, company_id, sleep, base_delay)
            result.briefed += 1
        except Exception as exc:
            result.failed += 1
            reason = f"{step}: {type(exc).__name__}"
            if isinstance(exc, httpx.HTTPStatusError):
                reason += f" HTTP {exc.response.status_code}"
            result.reasons[reason] = result.reasons.get(reason, 0) + 1
    return result
