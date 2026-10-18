"""Issue RS256 user and service JWTs.

User tokens carry the tenant, role, permissions, and scopes of one membership.
Service tokens identify an internal service with ``role = "service"``. Both are
short lived and verifiable through the JWKS. Relates to ``iam.keys`` and
``iam.rbac``.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import time
import uuid
from typing import Any

import jwt
from django.conf import settings

from services.identity.iam.keys import signing_key
from services.identity.iam.models import MembershipScope, TenantMembership
from services.identity.iam.rbac import permissions_for


def _sign(claims: dict[str, Any], ttl: int) -> str:
    now = int(time.time())
    payload = {
        "iss": settings.IDENTITY_ISSUER,
        "aud": settings.IDENTITY_USER_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        **claims,
    }
    key = signing_key()
    return jwt.encode(
        payload, key.private_pem, algorithm="RS256", headers={"kid": key.kid, "typ": "JWT"}
    )


def issue_user_token(membership: TenantMembership) -> str:
    """Return an access token for an active membership."""
    scopes = [
        {"city": s.city, "service_line": s.service_line}
        for s in MembershipScope.objects.filter(
            tenant_id=membership.tenant_id, user_id=membership.user_id
        ).order_by("city", "service_line")
    ]
    return _sign(
        {
            "sub": str(membership.user_id),
            "tenant_id": str(membership.tenant_id),
            "role": membership.role,
            "permissions": sorted(permissions_for(membership.role)),
            "scopes": scopes,
        },
        settings.IDENTITY_USER_TOKEN_TTL_SECONDS,
    )


def issue_service_token(service: str) -> str:
    """Return a service token for the named internal service."""
    return _sign(
        {"sub": f"service:{service}", "role": "service", "permissions": []},
        settings.IDENTITY_SERVICE_TOKEN_TTL_SECONDS,
    )
