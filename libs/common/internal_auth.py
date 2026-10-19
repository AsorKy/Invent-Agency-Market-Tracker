"""Sign and validate service tokens for service-to-service calls (REQ-SEC-005).

A signed token has the form ``v1.<service>.<expires-at>.<hex-hmac>`` where the
HMAC-SHA256 covers ``v1.<service>.<expires-at>`` and is keyed with
``INTERNAL_SERVICE_TOKEN`` from ``libs.common.settings``. Callers such as the
gateway mint a short-lived token per request with ``sign_service_token``;
FastAPI routes in ``services/*/app/main.py`` validate it through
``require_internal_token``. Tenant headers (``X-Tenant-ID``) are honoured only
behind a valid token, and ``require_gateway_token`` additionally pins the
caller to the gateway. For compatibility with callers that still send the raw
shared secret, ``require_internal_token`` also accepts that value (legacy mode).
"""

import hashlib
import hmac
import time

from fastapi import Header, HTTPException

from libs.common.settings import get_settings

TOKEN_VERSION = "v1"
DEFAULT_TTL_SECONDS = 60
GATEWAY_SERVICE = "gateway"


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


def caller_service(token: str | None) -> str | None:
    """Return the caller of a valid token; the legacy raw secret maps to ``legacy``."""

    if not token:
        return None
    secret = get_settings().internal_service_token
    service = verify_service_token(token, secret)
    if service is not None:
        return service
    if hmac.compare_digest(token.encode("utf-8"), secret.encode("utf-8")):
        return "legacy"
    return None


async def require_internal_token(
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> None:
    """Refuse any request without a valid signed (or legacy) service token."""

    if caller_service(x_service_token) is None:
        raise HTTPException(status_code=401, detail="invalid_service_token")


async def require_gateway_token(
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> None:
    """Accept only a valid signed token issued by the gateway."""

    if caller_service(x_service_token) != GATEWAY_SERVICE:
        raise HTTPException(status_code=401, detail="invalid_service_token")
