"""URL routes for the identity service.

The admin lives here but ``iam.middleware.InternalAdminOnlyMiddleware`` hides it
from any request that did not arrive over the internal network.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

from django.contrib import admin
from django.urls import path

from services.identity.iam import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health", views.health),
    path("ready", views.ready),
    path(".well-known/jwks.json", views.jwks),
    path("internal/identity/service-token", views.ServiceTokenView.as_view()),
    path("internal/identity/tokens/user", views.UserTokenView.as_view()),
]
