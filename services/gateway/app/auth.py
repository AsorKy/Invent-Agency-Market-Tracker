"""Verify user JWTs against the identity JWKS and apply tenant and scope rules.

``JwksCache`` fetches ``/.well-known/jwks.json`` from identity and keeps it for
10 minutes; an unknown ``kid`` triggers at most one refresh per 30 seconds and a
failed refresh serves the stale copy, so valid tokens keep working while
identity is down (ARCH-0001 E1.10). ``verify_token`` returns ``Claims`` or
raises ``AuthError``. Relates to ``services/identity`` (issuer) and
``services/gateway/app/main.py``.
"""

import json
import os
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

import jwt

JWKS_TTL_SECONDS = 600.0
JWKS_UNKNOWN_KID_REFRESH_SECONDS = 30.0
AUDIENCE = "imt-api"
SCOPED_ROLES = frozenset({"intake", "bd_rep"})


class AuthError(Exception):
    """Raised when a token is missing, invalid, or not allowed on the public API."""


@dataclass(frozen=True, slots=True)
class Claims:
    user_id: str
    tenant_id: str
    role: str
    scopes: tuple[tuple[str, str], ...]
    token: str


class JwksCache:
    def __init__(
        self,
        url: Callable[[], str],
        client_factory: Callable[[], AbstractAsyncContextManager[Any]],
        error_types: Callable[[], tuple[type[BaseException], ...]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._client_factory = client_factory
        self._error_types = error_types
        self._clock = clock
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: float | None = None
        self._last_unknown_refresh = float("-inf")
        self.fetch_count = 0

    async def _fetch(self) -> None:
        async with self._client_factory() as client:
            try:
                response = await client.get(self._url())
                response.raise_for_status()
                keys = response.json()["keys"]
            except self._error_types() + (KeyError, ValueError):
                if self._keys:
                    return  # keep serving the stale copy
                raise AuthError("jwks_unavailable") from None
        self.fetch_count += 1
        self._keys = {k["kid"]: k for k in keys if "kid" in k}
        self._fetched_at = self._clock()

    async def key_for(self, kid: str) -> Any:
        """Return the public key for ``kid``, refreshing the cache when needed."""
        now = self._clock()
        if self._fetched_at is None or now - self._fetched_at >= JWKS_TTL_SECONDS:
            await self._fetch()
        if kid not in self._keys and now - self._last_unknown_refresh >= (
            JWKS_UNKNOWN_KID_REFRESH_SECONDS
        ):
            self._last_unknown_refresh = now
            await self._fetch()
        jwk = self._keys.get(kid)
        if jwk is None:
            raise AuthError("unknown_key")
        return jwt.PyJWK(jwk).key


async def verify_token(token: str, cache: JwksCache) -> Claims:
    """Verify an RS256 user token and return its claims."""
    try:
        kid = jwt.get_unverified_header(token).get("kid", "")
        key = await cache.key_for(str(kid))
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=os.environ.get("IDENTITY_ISSUER", "imt-identity"),
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError:
        raise AuthError("invalid_token") from None
    tenant_id = payload.get("tenant_id")
    role = payload.get("role")
    if not tenant_id or not role or role == "service":
        raise AuthError("not_a_user_token")
    scopes = tuple(
        (str(s.get("city", "")), str(s.get("service_line", "")))
        for s in payload.get("scopes", [])
        if isinstance(s, dict)
    )
    return Claims(str(payload["sub"]), str(tenant_id), str(role), scopes, token)


def scope_allows(claims: Claims, city: str | None, service_line: str | None) -> bool:
    """Return whether the explicit filters stay inside the user's assigned scopes."""
    if claims.role not in SCOPED_ROLES:
        return True
    return any(
        (city is None or c in ("", city)) and (service_line is None or s in ("", service_line))
        for c, s in claims.scopes
    )


def identity_headers(claims: Claims) -> dict[str, str]:
    """Return the trusted headers the gateway sets after a successful verification."""
    return {
        "X-Tenant-Id": claims.tenant_id,
        "X-User-Id": claims.user_id,
        "X-User-Role": claims.role,
        "X-User-Scopes": json.dumps(
            [{"city": c, "service_line": s} for c, s in claims.scopes], separators=(",", ":")
        ),
        "Authorization": f"Bearer {claims.token}",
    }
