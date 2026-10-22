"""Fetch the per-tenant score rules from ``core`` over its internal API (REQ-ML-001).

``ml_app`` has no grant on the ``core`` schema (INV-01), so ``HttpCoreRules`` calls
``GET /internal/core/admin/score-rules`` with a signed token for service ``ml`` and the
``X-Tenant-Id`` header, with an explicit timeout. A transport failure or a 5xx answer raises
``CoreRulesUnavailable``; the caller then scores with default weights. Relates to
``repository.py`` (``load_weights``) and ``services/core/app/score_rules.py`` (the route).
"""

from typing import Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from libs.common.internal_auth import service_token_header

ML_SERVICE = "ml"


class CoreRulesUnavailable(Exception):
    """Raised when core cannot be reached, answers 5xx, or sends an unreadable body."""


class ScoreRule(BaseModel):
    rule_name: str
    weight: float
    active: bool


class ScoreRulesBody(BaseModel):
    rules: list[ScoreRule]
    version: int = 0


class CoreRules(Protocol):
    """Interface to the tenant score rules held by core."""

    async def get_rules(self, tenant_id: UUID) -> list[tuple[str, float, bool]]:
        """Return ``(rule_name, weight, active)`` rows; raise ``CoreRulesUnavailable``."""
        ...

    async def close(self) -> None: ...


class HttpCoreRules:
    """Call the core internal endpoint with the service token."""

    def __init__(self, base_url: str, token: str, timeout_seconds: float = 2.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)
        self._secret = token

    async def close(self) -> None:
        await self._client.aclose()

    async def get_rules(self, tenant_id: UUID) -> list[tuple[str, float, bool]]:
        headers = {**service_token_header(ML_SERVICE, self._secret), "X-Tenant-Id": str(tenant_id)}
        try:
            response = await self._client.get("/internal/core/admin/score-rules", headers=headers)
        except httpx.HTTPError as exc:
            raise CoreRulesUnavailable(str(exc)) from exc
        if response.status_code != 200:
            raise CoreRulesUnavailable(f"core answered {response.status_code}")
        try:
            body = ScoreRulesBody.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise CoreRulesUnavailable("core sent an unreadable body") from exc
        return [(r.rule_name, r.weight, r.active) for r in body.rules]
