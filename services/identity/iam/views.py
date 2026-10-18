"""HTTP endpoints of the identity service.

Public-by-design: ``/health``, ``/ready`` and the JWKS. Internal (guarded by
``X-Service-Token``): service-token issuance and user-token issuance, the latter
to be driven by the session layer (T-031).
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from services.identity.iam.keys import jwks_document
from services.identity.iam.models import TenantMembership
from services.identity.iam.tokens import issue_service_token, issue_user_token


def health(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "service": "identity"})


def ready(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ready", "service": "identity"})


def jwks(_request: HttpRequest) -> JsonResponse:
    response = JsonResponse(jwks_document())
    response["Cache-Control"] = "public, max-age=300"
    return response


class ServiceTokenView(APIView):
    """Issue a service token for ``{"service": "<name>"}``."""

    def post(self, request: Request) -> Response:
        service = str(request.data.get("service", "")).strip()
        if not service.isidentifier():
            return Response({"detail": "invalid_service"}, status=400)
        return Response({"access_token": issue_service_token(service), "token_type": "Bearer"})


class UserTokenView(APIView):
    """Issue a user token for ``{"user_id", "tenant_id"}`` with an active membership."""

    def post(self, request: Request) -> Response:
        try:
            membership = TenantMembership.objects.select_related("user", "tenant").get(
                user_id=request.data.get("user_id"),
                tenant_id=request.data.get("tenant_id"),
                active=True,
            )
        except (TenantMembership.DoesNotExist, ValidationError, ValueError, TypeError):
            return Response({"detail": "membership_not_found"}, status=404)
        if membership.user.status != "active" or membership.tenant.status != "active":
            return Response({"detail": "account_inactive"}, status=403)
        return Response({"access_token": issue_user_token(membership), "token_type": "Bearer"})
