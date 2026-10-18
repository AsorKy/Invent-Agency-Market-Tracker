"""DRF permission: internal routes require the shared ``X-Service-Token``.

Mirrors ``libs.common.internal_auth`` (FastAPI) for the Django service, using a
constant-time comparison.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import secrets
from typing import Any

from django.conf import settings
from rest_framework.permissions import BasePermission
from rest_framework.request import Request


class InternalServiceToken(BasePermission):
    """Allow only callers presenting the internal service token."""

    def has_permission(self, request: Request, view: Any) -> bool:
        presented = request.headers.get("X-Service-Token", "")
        expected: str = settings.IDENTITY_INTERNAL_SERVICE_TOKEN
        return bool(presented) and secrets.compare_digest(presented, expected)
