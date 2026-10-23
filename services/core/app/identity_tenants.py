"""List the active tenant ids from the identity service (D-36, T-194).

Core never reads another schema (INV-01) and ``core.tenant_config`` is under row level security,
so the retention job asks ``GET /internal/identity/tenants`` with a signed service token of
``core``. The answer carries ids only. It relates to ``retention.py`` and ``main.py``.
"""

from uuid import UUID

import httpx

from libs.common.internal_auth import service_token_header

TENANTS_PATH = "/internal/identity/tenants"
TIMEOUT_SECONDS = 10.0


async def list_tenant_ids(
    identity_url: str, secret: str, *, client: httpx.AsyncClient | None = None
) -> list[UUID]:
    """Return the active tenant ids; raise ``RuntimeError`` when identity cannot answer."""
    headers = service_token_header("core", secret)
    owned = client is None
    http = client or httpx.AsyncClient(base_url=identity_url, timeout=TIMEOUT_SECONDS)
    try:
        response = await http.get(TENANTS_PATH, headers=headers)
        response.raise_for_status()
        return [UUID(str(item)) for item in response.json()["tenant_ids"]]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("identity_tenants_unavailable") from exc
    finally:
        if owned:
            await http.aclose()
