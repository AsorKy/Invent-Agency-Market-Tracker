"""DRF permission: internal routes require a signed ``X-Service-Token``.

Mirrors ``libs.common.internal_auth`` (FastAPI) for the Django service: the
token must be a valid, unexpired signed token (REQ-SEC-005). The raw shared
secret is not accepted.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import hmac
import os
from typing import Any

from django.conf import settings
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from libs.common.internal_auth import verify_service_token


class InternalServiceToken(BasePermission):
    """Allow only callers presenting a valid signed service token."""

    def has_permission(self, request: Request, view: Any) -> bool:
        presented = request.headers.get("X-Service-Token", "")
        secret: str = settings.IDENTITY_INTERNAL_SERVICE_TOKEN
        return bool(presented) and verify_service_token(presented, secret) is not None


MINT_CREDENTIAL_VARIABLE = "N8N_SOCIOECO_MINT_CREDENTIAL"
MINT_CREDENTIAL_HEADER = "X-Mint-Credential"


class DedicatedMintCredential(BasePermission):
    """Allow only the single-purpose n8n mint credential (D-33).

    The credential is distinct from ``INTERNAL_SERVICE_TOKEN``. An empty or unset
    variable refuses every request. The comparison is constant time.
    """

    def has_permission(self, request: Request, view: Any) -> bool:
        expected = os.environ.get(MINT_CREDENTIAL_VARIABLE, "")
        presented = request.headers.get(MINT_CREDENTIAL_HEADER, "")
        if not expected or not presented or not expected.isascii() or not presented.isascii():
            return False
        return hmac.compare_digest(presented.encode("ascii"), expected.encode("ascii"))
