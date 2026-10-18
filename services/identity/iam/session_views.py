"""Login, validation, profile, and logout endpoints for browser sessions.

Reached through the gateway with ``X-Service-Token``; the gateway forwards the
browser cookies. Login is exempt from CSRF at the gateway because it issues the
CSRF cookie. Relates to ``iam.sessions`` and ``iam.tokens``.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

from typing import Any

from django.contrib.auth.hashers import check_password, make_password
from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from services.identity.iam import sessions
from services.identity.iam.models import Credential, TenantMembership, UserAccount
from services.identity.iam.rbac import permissions_for
from services.identity.iam.tokens import issue_user_token

_DUMMY_HASH = make_password("not-a-real-password")


def _profile(membership: TenantMembership, user: UserAccount, session: Any) -> dict[str, Any]:
    return {
        "user_id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "tenant_id": str(membership.tenant_id),
        "role": membership.role,
        "permissions": sorted(permissions_for(membership.role)),
        "expires_at": sessions.expires_at(session).isoformat(),
    }


def _pick_membership(user: UserAccount, tenant_slug: str) -> TenantMembership | None:
    memberships = TenantMembership.objects.select_related("tenant").filter(
        user_id=user.id, active=True, tenant__status="active"
    )
    if tenant_slug:
        memberships = memberships.filter(tenant__slug=tenant_slug)
    found = list(memberships[:2])
    return found[0] if len(found) == 1 else None


class LoginView(APIView):
    """Verify email and password, then start a session and set the cookies."""

    def post(self, request: Request) -> Response:
        email = str(request.data.get("email", "")).strip()
        password = str(request.data.get("password", ""))
        tenant_slug = str(request.data.get("tenant", "")).strip()
        user = UserAccount.objects.filter(email__iexact=email, status="active").first()
        credential = Credential.objects.filter(user_id=user.id).first() if user else None
        # Always hash once so unknown users cost the same as wrong passwords.
        valid = check_password(password, credential.password_hash if credential else _DUMMY_HASH)
        membership = _pick_membership(user, tenant_slug) if user and credential and valid else None
        if membership is None or user is None:
            return Response({"detail": "invalid_credentials"}, status=401)
        session, raw = sessions.create_session(membership)
        response = Response(_profile(membership, user, session))
        sessions.set_session_cookies(response, session, raw)
        return response


class _SessionView(APIView):
    def load(self, request: Request) -> tuple[Any, TenantMembership, UserAccount] | None:
        session = sessions.validate_session(sessions.request_session_cookie(request._request))
        if session is None:
            return None
        membership = (
            TenantMembership.objects.select_related("user", "tenant")
            .filter(user_id=session.user_id, tenant_id=session.tenant_id, active=True)
            .first()
        )
        if membership is None or membership.user.status != "active":
            return None
        return session, membership, membership.user


class MeView(_SessionView):
    """Return the profile of the session user and renew the cookies."""

    def get(self, request: Request) -> Response:
        loaded = self.load(request)
        if loaded is None:
            response = Response({"detail": "authentication_required"}, status=401)
            sessions.clear_session_cookies(response)
            return response
        session, membership, user = loaded
        response = Response(_profile(membership, user, session))
        sessions.set_session_cookies(
            response, session, sessions.request_session_cookie(request._request) or ""
        )
        return response


class ValidateView(_SessionView):
    """Gateway hook: validate the cookie and return a short-lived access token."""

    def get(self, request: Request) -> Response:
        loaded = self.load(request)
        if loaded is None:
            return Response({"detail": "authentication_required"}, status=401)
        _session, membership, _user = loaded
        return Response({"access_token": issue_user_token(membership), "token_type": "Bearer"})


class LogoutView(APIView):
    """Invalidate the session server-side and expire the cookies."""

    def post(self, request: Request) -> HttpResponse:
        sessions.revoke_session(sessions.request_session_cookie(request._request))
        response = HttpResponse(status=204)
        sessions.clear_session_cookies(response)
        return response


def set_password(user_id: Any, password: str) -> None:
    """Store a hashed password for ``user_id`` (used by the dev-user command)."""
    Credential.objects.update_or_create(
        user_id=user_id, defaults={"password_hash": make_password(password)}
    )
