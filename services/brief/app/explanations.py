"""Optional explanation port for the Brief (REQ-LNS-007).

``brief`` must not import ``services.ml``, so the explanation payload built by
``services/ml/app/explain.py::build_explanation`` is read through this port.
``HttpExplanationSource`` calls the ml internal API with the service token and
an explicit timeout. Every failure (transport, status, shape) returns ``None``
so a brief stays valid without drivers and counterfactuals. It relates to
``service.py`` and ``main.py``.
"""

import logging
from typing import Any, Protocol
from uuid import UUID

import httpx

from libs.common.internal_auth import service_token_header

logger = logging.getLogger(__name__)

BRIEF_SERVICE = "brief"
MAX_DRIVERS = 10


class ExplanationSource(Protocol):
    """Interface to the explanation payload held by the ml service."""

    async def get_explanation(
        self, company_id: UUID, tenant_id: UUID | None
    ) -> dict[str, Any] | None:
        """Return ``{"drivers": [...], "counterfactual": {...}}`` or ``None``."""
        ...


def normalize_explanation(body: object) -> dict[str, Any] | None:
    """Keep only well-formed ``drivers`` and ``counterfactual``; ``None`` when empty."""
    if not isinstance(body, dict):
        return None
    drivers = body.get("drivers")
    counterfactual = body.get("counterfactual")
    clean_drivers = (
        [d for d in drivers[:MAX_DRIVERS] if isinstance(d, dict)]
        if isinstance(drivers, list)
        else None
    )
    clean_cf = counterfactual if isinstance(counterfactual, dict) else None
    if not clean_drivers and clean_cf is None:
        return None
    return {"drivers": clean_drivers or None, "counterfactual": clean_cf}


class HttpExplanationSource:
    """Call the ml internal explanation endpoint; never raises."""

    def __init__(self, base_url: str, token: str, timeout_seconds: float = 1.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)
        self._secret = token

    async def close(self) -> None:
        await self._client.aclose()

    async def get_explanation(
        self, company_id: UUID, tenant_id: UUID | None
    ) -> dict[str, Any] | None:
        headers = service_token_header(BRIEF_SERVICE, self._secret)
        if tenant_id:
            headers["X-Tenant-Id"] = str(tenant_id)
        try:
            response = await self._client.get(
                f"/internal/ml/explanations/{company_id}", headers=headers
            )
            if response.status_code != 200:
                return None
            return normalize_explanation(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("brief explanation unavailable (%s)", type(exc).__name__)
            return None
