"""Fetch the per-tenant demo score features from ``core`` over its internal API (REQ-LNS-007).

``ml_app`` has no grant on the ``core`` schema (INV-01), so ``HttpCoreFeatures`` calls
``GET /internal/core/admin/score-features`` with a signed token for service ``ml``, the
``X-Tenant-Id`` header of the tenant it was asked for, and an explicit timeout. A transport
failure, a 5xx answer or an unreadable body raises ``CoreFeaturesUnavailable``. Relates to
``backfill.py`` (the caller) and ``services/core/app/score_features.py`` (the route).
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from libs.common.internal_auth import service_token_header

ML_SERVICE = "ml"


class CoreFeaturesUnavailable(Exception):
    """Raised when core cannot be reached, answers an error, or sends an unreadable body."""


class CompanyFeatures(BaseModel):
    company_id: UUID
    icp_fit: float
    signal_strength: float
    territory_opportunity: float
    source_version: str
    parent_event_id: UUID | None = None


class FeaturesPage(BaseModel):
    companies: list[CompanyFeatures]
    next_after: UUID | None = None


@dataclass
class CoreFeaturesPage:
    companies: list[CompanyFeatures]
    next_after: UUID | None


class CoreFeatures(Protocol):
    """Interface to the tenant score features held by core."""

    async def get_page(self, tenant_id: UUID, limit: int, after: UUID | None) -> CoreFeaturesPage:
        """Return one page; raise ``CoreFeaturesUnavailable``."""
        ...

    async def close(self) -> None: ...


class HttpCoreFeatures:
    """Call the core internal endpoint with the service token."""

    def __init__(self, base_url: str, token: str, timeout_seconds: float = 10.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)
        self._secret = token

    async def close(self) -> None:
        await self._client.aclose()

    async def get_page(self, tenant_id: UUID, limit: int, after: UUID | None) -> CoreFeaturesPage:
        headers = {**service_token_header(ML_SERVICE, self._secret), "X-Tenant-Id": str(tenant_id)}
        params: dict[str, str | int] = {"limit": limit}
        if after is not None:
            params["after"] = str(after)
        try:
            response = await self._client.get(
                "/internal/core/admin/score-features", headers=headers, params=params
            )
        except httpx.HTTPError as exc:
            raise CoreFeaturesUnavailable(str(exc)) from exc
        if response.status_code != 200:
            raise CoreFeaturesUnavailable(f"core answered {response.status_code}")
        try:
            body = FeaturesPage.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise CoreFeaturesUnavailable("core sent an unreadable body") from exc
        return CoreFeaturesPage(companies=body.companies, next_after=body.next_after)
