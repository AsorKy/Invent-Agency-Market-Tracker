"""DRF permission: internal routes require a signed ``X-Service-Token``.

Mirrors ``libs.common.internal_auth`` (FastAPI) for the Django service: the
token must be a valid, unexpired signed token (REQ-SEC-005). The raw shared
secret is not accepted.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

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
