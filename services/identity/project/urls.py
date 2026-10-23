"""URL routes for the identity service.

The admin lives here but ``iam.middleware.InternalAdminOnlyMiddleware`` hides it
from any request that did not arrive over the internal network.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

from django.contrib import admin
from django.urls import path

from services.identity.iam import session_views, views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health", views.health),
    path("ready", views.ready),
    path(".well-known/jwks.json", views.jwks),
    path("internal/identity/service-token", views.ServiceTokenView.as_view()),
    path("internal/identity/mint/socioeco", views.SocioecoMintView.as_view()),
    path("internal/identity/tenants", views.TenantListView.as_view()),
    path("internal/identity/tokens/user", views.UserTokenView.as_view()),
    path("internal/identity/auth/session", session_views.LoginView.as_view()),
    path("internal/identity/me", session_views.MeView.as_view()),
    path("internal/identity/auth/validate", session_views.ValidateView.as_view()),
    path("internal/identity/logout", session_views.LogoutView.as_view()),
]
