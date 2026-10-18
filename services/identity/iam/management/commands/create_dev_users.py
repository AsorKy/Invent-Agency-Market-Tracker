"""Create one development user per role with passwords taken from the environment.

Reads ``DEV_USER_PASSWORD_<ROLE>`` through ``libs.common.settings``. Roles with
no password configured are skipped. The command refuses to run in the
production profile. Emails are ``<role>@dev.local``. Safe to re-run.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from libs.common.settings import get_settings
from services.identity.iam.models import Tenant, TenantMembership, UserAccount
from services.identity.iam.rbac import ROLES
from services.identity.iam.session_views import set_password


class Command(BaseCommand):
    help = "Create one development user per role (passwords from DEV_USER_PASSWORD_<ROLE>)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--tenant", default="dev", help="Tenant slug to create or reuse.")

    def handle(self, *args: Any, **options: Any) -> None:
        settings = get_settings()
        if settings.app_profile == "production":
            raise CommandError("create_dev_users is disabled in the production profile")
        tenant, _ = Tenant.objects.get_or_create(
            slug=options["tenant"], defaults={"display_name": "Development"}
        )
        for role in ROLES:
            password = getattr(settings, f"dev_user_password_{role}")
            if not password:
                self.stdout.write(f"skipped {role}: DEV_USER_PASSWORD_{role.upper()} is not set")
                continue
            email = f"{role}@dev.local"
            user = UserAccount.objects.filter(email__iexact=email).first()
            if user is None:
                user = UserAccount.objects.create(email=email, display_name=f"Dev {role}")
            TenantMembership.objects.update_or_create(
                tenant=tenant, user=user, defaults={"role": role, "active": True}
            )
            set_password(user.id, password)
            self.stdout.write(f"ready {role}: {email}")
