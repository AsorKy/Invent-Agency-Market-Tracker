"""Provide configuration-gated security checks for the public gateway.

The module relates to ``services/gateway/app/main.py`` and the future
``identity`` service. It enforces the v2.2 ``X-Edge-Secret`` boundary and
double-submit CSRF checks when their environment configuration is present,
while leaving JWT validation behind an explicit hook until Identity exists.
"""

import hmac
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256

from libs.common.settings import Settings, get_settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_COOKIE_NAME = "__Host-csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
EDGE_SECRET_HEADER_NAME = "X-Edge-Secret"
CSRF_BOOTSTRAP_PATH = "/api/v1/auth/session"


@dataclass(frozen=True, slots=True)
class GatewaySecurityConfig:
    """Capture security gates without adding settings fields to other services."""

    edge_secret: str | None
    csrf_enabled: bool
    identity_validation_enabled: bool
    edge_secret_required: bool

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "GatewaySecurityConfig":
        """Load gateway security gates from centralized application settings."""

        configured = settings or get_settings()
        return cls(
            edge_secret=configured.edge_secret or None,
            csrf_enabled=configured.csrf_required,
            identity_validation_enabled=configured.identity_validation_enabled,
            edge_secret_required=configured.edge_secret_required,
        )


def valid_edge_secret(
    headers: Mapping[str, str], expected: str | None, *, required: bool = False
) -> bool:
    """Return true when the configured edge secret is absent or matches safely."""

    if expected is None:
        return not required
    provided = headers.get(EDGE_SECRET_HEADER_NAME) or headers.get("x-edge-secret")
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def csrf_is_required(method: str, path: str, config: GatewaySecurityConfig) -> bool:
    """Decide whether a request needs a double-submit token.

    The login bootstrap is exempt because it is the operation that obtains the
    initial CSRF cookie. Identity must still apply its own login protections
    when it is introduced.
    """

    return (
        config.csrf_enabled and method.upper() not in SAFE_METHODS and path != CSRF_BOOTSTRAP_PATH
    )


def valid_double_submit_csrf(cookies: Mapping[str, str], headers: Mapping[str, str]) -> bool:
    """Return true only when the CSRF cookie and header contain the same token."""

    cookie_token = cookies.get(CSRF_COOKIE_NAME)
    header_token = headers.get(CSRF_HEADER_NAME) or headers.get("x-csrf-token")
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)


class IdentityValidationNotConfigured(RuntimeError):
    """Signal that JWT validation was enabled before an Identity hook was installed."""


IdentityValidator = Callable[[object], Awaitable[None]]


async def validate_identity_if_enabled(
    subject: object,
    validator: IdentityValidator | None,
    config: GatewaySecurityConfig,
) -> None:
    """Run the future Identity validator or fail closed when explicitly enabled."""

    if not config.identity_validation_enabled:
        return
    if validator is None:
        raise IdentityValidationNotConfigured
    await validator(subject)


def valid_signed_webhook(
    body: bytes,
    *,
    signature: str | None,
    timestamp: str | None,
    secret: str | None,
    replay_window_seconds: int,
    now: float | None = None,
) -> bool:
    """Validate a timestamped HMAC-SHA256 webhook when a secret is configured.

    The signed message is ``<timestamp>.<raw-body>`` and the signature may be
    sent as a bare hexadecimal digest or as ``sha256=<digest>``. A missing
    secret disables verification only for local bootstrap environments; once a
    secret is configured, both headers and the replay window are mandatory.
    """

    if not secret:
        return True
    if not signature or not timestamp:
        return False
    try:
        timestamp_value = float(timestamp)
    except ValueError:
        return False
    current_time = time.time() if now is None else now
    if abs(current_time - timestamp_value) > replay_window_seconds:
        return False
    provided = signature.removeprefix("sha256=")
    message = timestamp.encode("ascii") + b"." + body
    expected = hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()
    return hmac.compare_digest(provided, expected)
