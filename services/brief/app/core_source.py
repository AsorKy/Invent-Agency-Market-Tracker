"""Read company facts and tenant rules from ``core`` for Brief generation.

``CoreSource`` is the injectable seam; ``HttpCoreSource`` is the production
implementation over ``core``'s internal HTTP API with an explicit timeout.
Any transport failure or 5xx answer becomes ``CoreUnavailable``, which the
Brief endpoints map to 503 ``db_unavailable``. It relates to ``main.py`` and
``service.py``.
"""

from typing import Protocol
from uuid import UUID

import httpx

from libs.common.internal_auth import service_token_header
from services.brief.app.models import BriefRules, CompanyFacts

BRIEF_SERVICE = "brief"


class CoreUnavailable(Exception):
    """Raised when ``core`` cannot be reached or answers with a server error."""


class CoreNotFound(Exception):
    """Raised when ``core`` does not know the company for the tenant."""


class CoreSource(Protocol):
    """Interface to the company facts and tenant rules held by ``core``."""

    async def get_facts(self, company_id: UUID, tenant_id: UUID | None) -> CompanyFacts: ...

    async def get_lead_company(self, lead_id: UUID, tenant_id: UUID | None) -> UUID:
        """Return the company id of a lead; raise ``CoreNotFound`` when unknown."""
        ...

    async def get_rules(self, tenant_id: UUID | None) -> tuple[BriefRules, int]:
        """Return the tenant ``brief_rules`` and the config version (0 for defaults)."""
        ...


class HttpCoreSource:
    """Call ``core`` internal endpoints with the service token."""

    def __init__(self, base_url: str, token: str, timeout_seconds: float = 2.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)
        self._secret = token

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, tenant_id: UUID | None) -> httpx.Response:
        headers = service_token_header(BRIEF_SERVICE, self._secret)
        if tenant_id:
            headers["X-Tenant-Id"] = str(tenant_id)
        try:
            response = await self._client.get(path, headers=headers)
        except httpx.HTTPError as exc:
            raise CoreUnavailable(str(exc)) from exc
        if response.status_code == 404:
            raise CoreNotFound(path)
        if response.status_code >= 500:
            raise CoreUnavailable(f"core answered {response.status_code}")
        response.raise_for_status()
        return response

    async def get_facts(self, company_id: UUID, tenant_id: UUID | None) -> CompanyFacts:
        response = await self._get(f"/internal/core/companies/{company_id}/brief-facts", tenant_id)
        return CompanyFacts(company_id=company_id, **response.json())

    async def get_lead_company(self, lead_id: UUID, tenant_id: UUID | None) -> UUID:
        response = await self._get(f"/internal/core/leads/{lead_id}", tenant_id)
        return UUID(str(response.json()["company_id"]))

    async def get_rules(self, tenant_id: UUID | None) -> tuple[BriefRules, int]:
        if tenant_id is None:
            return BriefRules(), 0
        response = await self._get("/internal/core/admin/tenant-config", tenant_id)
        body = response.json()
        return BriefRules(**(body.get("brief_rules") or {})), int(body.get("version", 0))
