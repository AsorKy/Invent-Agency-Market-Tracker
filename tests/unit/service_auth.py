"""Signed service-token headers for tests (REQ-SEC-005).

Tokens live for one day here so module-level header constants stay valid for
the whole test run; production senders mint a 60 second token per request.
"""

from libs.common.internal_auth import sign_service_token
from libs.common.settings import get_settings

TEST_TTL_SECONDS = 86_400


def service_headers(service: str = "gateway", **extra: str) -> dict[str, str]:
    """Return an ``X-Service-Token`` header signed for ``service`` plus ``extra`` headers."""
    token = sign_service_token(
        service, get_settings().internal_service_token, ttl_seconds=TEST_TTL_SECONDS
    )
    return {"X-Service-Token": token, **extra}
