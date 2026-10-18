"""Create the credential and auth_session tables owned by the identity service.

The SQL-owned tables (tenant, user_account, memberships) are not part of this
migration state. These two tables are Django-managed because the SQL migrations
do not define them (T-031).
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

from django.db import migrations, models

from services.identity.iam.models import _table


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="Credential",
            fields=[
                ("user_id", models.UUIDField(primary_key=True, serialize=False)),
                ("password_hash", models.TextField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": _table("credential")},
        ),
        migrations.CreateModel(
            name="AuthSession",
            fields=[
                ("key_hash", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("user_id", models.UUIDField(db_index=True)),
                ("tenant_id", models.UUIDField()),
                ("csrf_token", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField()),
                ("last_seen_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(null=True)),
            ],
            options={"db_table": _table("auth_session")},
        ),
    ]
