"""Post one alert to the hot lead webhook when a lead becomes Hot (REQ-ALR-001).

``HotAlertNotifier`` is called after the transaction that made a lead Hot has committed, by
``qualification.handle_score_event`` and ``leads.change_status``. Calling it only schedules a
background task, so the commit is never blocked and a failed post never rolls anything back.
The task posts to ``HOT_LEAD_WEBHOOK_URL`` (n8n forwards it to Telegram, a mock locally) and
retries with exponential backoff until the 60 second deadline counted from the commit. Delivery
is deduplicated by ``(lead_id, version)`` in process memory, so a restart may repeat an alert.
The poster, clock and sleep are injected so tests need no network. Unset URL disables alerts.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

DEADLINE_SECONDS = 60.0
FIRST_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 16.0
POST_TIMEOUT_SECONDS = 5.0

Poster = Callable[[str, dict[str, Any]], Awaitable[bool]]
HotCallback = Callable[["HotAlert"], Awaitable[None]]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HotAlert:
    tenant_id: UUID | None
    lead_id: UUID
    company_id: UUID
    version: int
    score: int | None
    committed_at: float  # clock reading taken when the Hot commit finished

    def body(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "lead_id": str(self.lead_id),
            "company_id": str(self.company_id),
            "version": self.version,
            "score": self.score,
            "stage": "Hot",
        }


async def http_poster(url: str, body: dict[str, Any]) -> bool:
    """Post ``body`` as JSON; return True on a 2xx answer and False on any failure."""
    try:
        async with httpx.AsyncClient(timeout=POST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body)
        return response.is_success
    except httpx.HTTPError:
        return False


class HotAlertNotifier:
    def __init__(
        self,
        url: str | None,
        *,
        poster: Poster = http_poster,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._url = url
        self._poster = poster
        self._clock = clock
        self._sleep = sleep
        self._seen: set[tuple[UUID, int]] = set()
        self._tasks: set[asyncio.Task[bool]] = set()

    async def __call__(self, alert: HotAlert) -> None:
        """Schedule the delivery and return at once (never raises)."""
        key = (alert.lead_id, alert.version)
        if not self._url or key in self._seen:
            return
        self._seen.add(key)
        task = asyncio.get_running_loop().create_task(self.deliver(alert))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def deliver(self, alert: HotAlert) -> bool:
        """Post with backoff until success or the deadline; return whether it was delivered."""
        assert self._url is not None
        deadline = alert.committed_at + DEADLINE_SECONDS
        delay = FIRST_DELAY_SECONDS
        while True:
            try:
                if await self._poster(self._url, alert.body()):
                    return True
            except Exception:
                _log.warning("hot alert post raised lead_id=%s", alert.lead_id)
            if self._clock() + delay > deadline:
                break
            await self._sleep(delay)
            delay = min(delay * 2, MAX_DELAY_SECONDS)
        self._seen.discard((alert.lead_id, alert.version))
        _log.error("hot alert not delivered lead_id=%s version=%s", alert.lead_id, alert.version)
        return False

    async def drain(self) -> None:
        """Wait for scheduled deliveries (used on shutdown and in tests)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


async def fire(callback: HotCallback | None, alerts: list[HotAlert]) -> None:
    """Hand committed Hot transitions to ``callback``; a failure is logged and swallowed."""
    if callback is None:
        return
    for alert in alerts:
        try:
            await callback(alert)
        except Exception:
            _log.exception("hot alert hand-off failed lead_id=%s", alert.lead_id)
