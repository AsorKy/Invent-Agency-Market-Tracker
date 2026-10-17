"""Validate the service token for internal routes.

FastAPI injects ``X-Service-Token`` into protected routes and compares it with
``INTERNAL_SERVICE_TOKEN`` using a constant-time comparison. It relates to
``libs.common.settings`` and the gateway, which adds the token when forwarding
requests to ``services/*/app/main.py``. It keeps internal APIs out of anonymous
access before user authentication is introduced with the frontend.
"""

import secrets

from fastapi import Header, HTTPException

from libs.common.settings import get_settings


async def require_internal_token(
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> None:
    expected = get_settings().internal_service_token
    if not x_service_token or not secrets.compare_digest(x_service_token, expected):
        raise HTTPException(status_code=401, detail="invalid_service_token")
