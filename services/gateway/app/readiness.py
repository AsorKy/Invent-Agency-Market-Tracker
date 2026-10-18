"""Track upstream readiness so only routes with a red upstream answer 503.

A background task polls each service ``/ready`` every ``interval`` seconds and
records a boolean. A service never polled counts as ready, so a cold gateway does
not reject traffic. Relates to ``services/gateway/app/main.py``.
"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS = 10.0


class UpstreamReadiness:
    def __init__(
        self,
        upstreams: Callable[[], dict[str, str]],
        client_factory: Callable[[], AbstractAsyncContextManager[Any]],
        error_types: Callable[[], tuple[type[BaseException], ...]],
        interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self._upstreams = upstreams
        self._client_factory = client_factory
        self._error_types = error_types
        self.interval = interval
        self.state: dict[str, bool] = {}

    def is_red(self, service: str) -> bool:
        """Return true only when the last poll found ``service`` not ready."""
        return self.state.get(service) is False

    async def refresh(self) -> None:
        """Poll every upstream once and store the results."""
        async with self._client_factory() as client:
            for name, base_url in self._upstreams().items():
                try:
                    response = await client.get(f"{base_url.rstrip('/')}/ready")
                    self.state[name] = bool(response.is_success)
                except self._error_types():
                    self.state[name] = False

    async def run(self) -> None:
        """Poll forever; the first poll happens after one interval."""
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001 - a poll failure must never kill the loop
                logger.warning("readiness_poll_failed")
