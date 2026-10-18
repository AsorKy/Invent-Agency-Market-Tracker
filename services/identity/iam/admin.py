"""Django admin registrations for tenants and users.

Membership and scope models use composite primary keys, which Django 5.2 cannot
register in the admin; they are managed through the internal API (T-031).
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

from django.contrib import admin

from services.identity.iam.models import Tenant, UserAccount

admin.site.register(Tenant)
admin.site.register(UserAccount)
