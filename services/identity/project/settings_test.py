"""SQLite settings for identity unit tests.

Relates to ``project.settings``. Tables are unqualified (``identity_tenant``)
and managed by Django so tests can create them without Postgres.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

from services.identity.project.settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
IDENTITY_SCHEMA_QUALIFIED = False
IDENTITY_MANAGED_TABLES = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
