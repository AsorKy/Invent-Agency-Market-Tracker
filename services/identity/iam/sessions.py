"""Server-side sliding sessions and cookie policy (REQ-IDN-002).

A session is an opaque random value held in an HttpOnly cookie; the database
keeps only its SHA-256. It expires after ``IDENTITY_SESSION_IDLE_SECONDS`` (30
minutes) without use and ``IDENTITY_SESSION_ABSOLUTE_SECONDS`` (12 hours) after
login, whichever comes first. Each successful validation renews the idle
window. Cookie names and attributes follow ``IDENTITY_PROFILE``: production uses
the ``__Host-`` prefix with ``Secure``; local drops both.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import hashlib
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from services.identity.iam.models import AuthSession, TenantMembership


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii", "replace")).hexdigest()


def cookie_names() -> tuple[str, str]:
    """Return ``(session_cookie, csrf_cookie)`` names for the active profile."""
    prefix = "__Host-" if settings.IDENTITY_PROFILE == "production" else ""
    return f"{prefix}session", f"{prefix}csrf"


def expires_at(session: AuthSession) -> datetime:
    """Return the moment the session stops being valid."""
    idle = session.last_seen_at + timedelta(seconds=settings.IDENTITY_SESSION_IDLE_SECONDS)
    absolute = session.created_at + timedelta(seconds=settings.IDENTITY_SESSION_ABSOLUTE_SECONDS)
    return min(idle, absolute)


def create_session(membership: TenantMembership) -> tuple[AuthSession, str]:
    """Create a session; return it with the raw cookie value."""
    raw = secrets.token_urlsafe(32)
    now = timezone.now()
    session = AuthSession.objects.create(
        key_hash=_hash(raw),
        user_id=membership.user_id,
        tenant_id=membership.tenant_id,
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
    )
    return session, raw


def validate_session(raw: str | None) -> AuthSession | None:
    """Return the live session for ``raw`` and renew its idle window, else ``None``."""
    if not raw:
        return None
    session = AuthSession.objects.filter(key_hash=_hash(raw), revoked_at__isnull=True).first()
    if session is None:
        return None
    now = timezone.now()
    if now >= expires_at(session):
        AuthSession.objects.filter(key_hash=session.key_hash).update(revoked_at=now)
        return None
    session.last_seen_at = now
    AuthSession.objects.filter(key_hash=session.key_hash).update(last_seen_at=now)
    return session


def revoke_session(raw: str | None) -> None:
    """Invalidate the session for ``raw`` if it exists."""
    if raw:
        AuthSession.objects.filter(key_hash=_hash(raw), revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )


def set_session_cookies(response: HttpResponse, session: AuthSession, raw: str) -> None:
    """Attach the session cookie (HttpOnly) and the double-submit CSRF cookie."""
    name, csrf_name = cookie_names()
    remaining = max(0, int((expires_at(session) - timezone.now()).total_seconds()))
    secure = settings.IDENTITY_PROFILE == "production"
    common = {"max_age": remaining, "path": "/", "secure": secure, "samesite": "Strict"}
    response.set_cookie(name, raw, httponly=True, **common)
    response.set_cookie(csrf_name, session.csrf_token, httponly=False, **common)


def clear_session_cookies(response: HttpResponse) -> None:
    """Expire both cookies in the browser."""
    secure = settings.IDENTITY_PROFILE == "production"
    for name in cookie_names():
        response.set_cookie(
            name, "", max_age=0, path="/", secure=secure, samesite="Strict", httponly=True
        )


def request_session_cookie(request: HttpRequest) -> str | None:
    """Return the raw session cookie value of a request."""
    return request.COOKIES.get(cookie_names()[0])
