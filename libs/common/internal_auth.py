"""Sign and validate service tokens for service-to-service calls (REQ-SEC-005).

A signed token has the form ``v1.<service>.<expires-at>.<hex-hmac>`` where the
HMAC-SHA256 covers ``v1.<service>.<expires-at>`` and is keyed with
``INTERNAL_SERVICE_TOKEN`` from ``libs.common.settings``. Callers such as the
gateway mint a short-lived token per request with ``sign_service_token``;
FastAPI routes in ``services/*/app/main.py`` validate it through
``require_internal_token``. Tenant headers (``X-Tenant-ID``) are honoured only
behind a valid token, ``require_gateway_token`` pins the caller to the gateway,
and ``require_service_token`` pins it to an explicit allow list of services.
The caller names ``seed`` and ``verify_live`` exist only outside production
(D-28): ``require_service_token`` answers 401 for them when the profile is
production. The raw shared secret is never accepted: every sender signs a token per request
(``service_token_header``) because a token lives only 60 seconds.
"""

import hashlib
import hmac
import time
from collections.abc import Awaitable, Callable

from fastapi import Header, HTTPException

from libs.common.settings import get_settings

TOKEN_VERSION = "v1"
DEFAULT_TTL_SECONDS = 60
GATEWAY_SERVICE = "gateway"
# Caller names that exist only outside production (REQ-SEC-005, D-28).
NON_PRODUCTION_CALLERS = frozenset({"seed", "verify_live"})
# Same list as ops/bootstrap_roles.py: anything else counts as production.
LOCAL_ENVIRONMENTS = frozenset({"local", "test", "ci", "dev", "development"})


def _mac(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("ascii"), hashlib.sha256).hexdigest()


def sign_service_token(
    service: str,
    secret: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Return a signed token for ``service`` that expires after ``ttl_seconds``."""

    if not service or "." in service or not service.isascii():
        raise ValueError("invalid service name")
    issued = time.time() if now is None else now
    expires = int(issued) + ttl_seconds
    message = f"{TOKEN_VERSION}.{service}.{expires}"
    return f"{message}.{_mac(secret, message)}"


def verify_service_token(token: str, secret: str, *, now: float | None = None) -> str | None:
    """Return the calling service name for a valid, unexpired token, else ``None``."""

    parts = token.split(".")
    if len(parts) != 4 or parts[0] != TOKEN_VERSION or not token.isascii():
        return None
    version, service, expires_text, signature = parts
    if not expires_text.isdigit() or not service:
        return None
    expected = _mac(secret, f"{version}.{service}.{expires_text}")
    if not hmac.compare_digest(signature, expected):
        return None
    current = time.time() if now is None else now
    if current > int(expires_text):
        return None
    return service


def service_token_header(service: str, secret: str) -> dict[str, str]:
    """Return the ``X-Service-Token`` header with a fresh token for ``service``."""

    return {"X-Service-Token": sign_service_token(service, secret)}


def is_production() -> bool:
    """Return True unless both the profile and the environment are non production."""

    settings = get_settings()
    if settings.app_profile == "production":
        return True
    return settings.environment.strip().lower() not in LOCAL_ENVIRONMENTS


def caller_service(token: str | None) -> str | None:
    """Return the calling service of a valid signed token, else ``None``."""

    if not token:
        return None
    return verify_service_token(token, get_settings().internal_service_token)


async def require_internal_token(
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> None:
    """Refuse any request without a valid signed service token."""

    if caller_service(x_service_token) is None:
        raise HTTPException(status_code=401, detail="invalid_service_token")


def require_service_token(*allowed: str) -> Callable[..., Awaitable[None]]:
    """Return a dependency that accepts only signed tokens from ``allowed`` services."""

    if not allowed:
        raise ValueError("allowed services must not be empty")
    permitted = frozenset(allowed)

    async def dependency(
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> None:
        caller = caller_service(x_service_token)
        if caller not in permitted or (caller in NON_PRODUCTION_CALLERS and is_production()):
            raise HTTPException(status_code=401, detail="invalid_service_token")

    return dependency


require_gateway_token = require_service_token(GATEWAY_SERVICE)
