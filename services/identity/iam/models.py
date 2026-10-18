"""Identity models mapped to the ``identity`` schema created by SQL migrations.

Relates to migrations/20261017_0002_arch_v22_foundations.sql: ``tenant``,
``user_account``, ``tenant_membership`` (role) and ``membership_scope``
(city and service line). Column names match the SQL so Django and the
migrations agree. Roles live on the membership, so one user can hold a
different role per tenant.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import uuid

from django.conf import settings
from django.db import models

from services.identity.iam.rbac import ROLES


def _table(name: str) -> str:
    """Return the table name, schema-qualified on Postgres."""
    return f'identity"."{name}' if settings.IDENTITY_SCHEMA_QUALIFIED else f"identity_{name}"


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.TextField(unique=True)
    display_name = models.TextField()
    status = models.TextField(default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table("tenant")
        managed = settings.IDENTITY_MANAGED_TABLES

    def __str__(self) -> str:
        return self.slug


class UserAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.TextField()
    display_name = models.TextField(null=True, blank=True)
    status = models.TextField(default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table("user_account")
        managed = settings.IDENTITY_MANAGED_TABLES

    def __str__(self) -> str:
        return self.email


class TenantMembership(models.Model):
    """A user's role inside a tenant. The SQL primary key is (tenant_id, user_id)."""

    pk = models.CompositePrimaryKey("tenant_id", "user_id")
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, db_column="tenant_id")
    user = models.ForeignKey(UserAccount, on_delete=models.PROTECT, db_column="user_id")
    role = models.TextField(choices=[(r, r) for r in ROLES])
    scopes = models.JSONField(default=dict)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table("tenant_membership")
        managed = settings.IDENTITY_MANAGED_TABLES


class MembershipScope(models.Model):
    """One allowed city and service line for a membership; empty string means any."""

    pk = models.CompositePrimaryKey("tenant_id", "user_id", "city", "service_line")
    tenant_id = models.UUIDField()
    user_id = models.UUIDField()
    city = models.TextField(default="")
    service_line = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = _table("membership_scope")
        managed = settings.IDENTITY_MANAGED_TABLES
