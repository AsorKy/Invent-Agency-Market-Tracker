"""Build the optional semantic summarizer for Brief from brief-local environment variables.

``BRIEF_SUMMARIZER_URL`` enables ``HttpSummarizer``; when it is unset Brief uses
only the rule-based summarizer. ``BRIEF_SUMMARIZER_TIMEOUT_SECONDS`` bounds each
call (default 5). Any transport error, non-2xx answer, or malformed body raises,
and ``BriefService`` then degrades to the rule-based digest with
``degraded=True`` (REQ-BRF-001, REQ-BRF-002). It relates to ``service.py`` and
``rule_based.py``.
"""

import os
from collections.abc import Mapping

import httpx

from services.brief.app.models import BriefContent, BriefRules, CompanyFacts
from services.brief.app.rule_based import Summarizer, SummaryResult

DEFAULT_TIMEOUT_SECONDS = 5.0


class HttpSummarizer:
    """Call an external summarizer service with the company facts and tenant rules."""

    def __init__(
        self,
        url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    async def summarize(self, facts: CompanyFacts, rules: BriefRules) -> SummaryResult:
        response = await self._client.post(
            self._url,
            json={
                "facts": facts.model_dump(mode="json"),
                "rules": rules.model_dump(mode="json"),
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        body = response.json()
        return SummaryResult(
            content=BriefContent(**body["content"]),
            version=str(body["version"]),
        )


def build_summarizer(environ: Mapping[str, str] | None = None) -> HttpSummarizer | None:
    """Return the configured summarizer, or ``None`` when none is configured."""
    env = os.environ if environ is None else environ
    url = env.get("BRIEF_SUMMARIZER_URL", "").strip()
    if not url:
        return None
    raw = env.get("BRIEF_SUMMARIZER_TIMEOUT_SECONDS", "").strip()
    timeout = float(raw) if raw else DEFAULT_TIMEOUT_SECONDS
    return HttpSummarizer(url, timeout)


__all__ = ["HttpSummarizer", "Summarizer", "build_summarizer"]
