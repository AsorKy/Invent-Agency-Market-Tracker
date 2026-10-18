"""Django settings for the identity service.

Relates to ``libs.common.settings`` (internal service token) and the
``identity`` Postgres schema created by SQL migrations. Tables are owned by the
SQL migrations (``IDENTITY_MANAGED_TABLES`` is false), so Django never runs
DDL against Postgres. ``project.settings_test`` flips the flag for SQLite.
Secrets come from the environment only.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import os
from pathlib import Path

from libs.common.settings import get_settings

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("IDENTITY_DJANGO_SECRET_KEY", "dev-only-not-a-secret")
DEBUG = os.environ.get("IDENTITY_DEBUG", "").lower() in {"1", "true"}
ALLOWED_HOSTS = [h for h in os.environ.get("IDENTITY_ALLOWED_HOSTS", "*").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin",
    "rest_framework",
    "services.identity.iam",
]

MIDDLEWARE = [
    "services.identity.iam.middleware.InternalAdminOnlyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "services.identity.project.urls"
WSGI_APPLICATION = "services.identity.project.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "imt"),
        "USER": os.environ.get("IDENTITY_DB_USER", "identity_app"),
        "PASSWORD": os.environ.get("IDENTITY_DB_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "OPTIONS": {"options": "-c search_path=identity,public"},
    }
}

# True: model tables use the "identity"."name" schema-qualified form (Postgres).
IDENTITY_SCHEMA_QUALIFIED = True
# True only for SQLite tests; production DDL belongs to the SQL migrations.
IDENTITY_MANAGED_TABLES = False

IDENTITY_INTERNAL_SERVICE_TOKEN = get_settings().internal_service_token
IDENTITY_ISSUER = os.environ.get("IDENTITY_ISSUER", "imt-identity")
IDENTITY_USER_AUDIENCE = "imt-api"
IDENTITY_USER_TOKEN_TTL_SECONDS = 15 * 60
IDENTITY_SERVICE_TOKEN_TTL_SECONDS = 5 * 60
# PEM text of the RS256 private key. Empty: an ephemeral key is generated
# (development and tests only).
IDENTITY_JWT_PRIVATE_KEY_PEM = os.environ.get("IDENTITY_JWT_PRIVATE_KEY_PEM", "")
# Public PEMs (separated by "|") of retired keys still published for rotation.
IDENTITY_JWT_PREVIOUS_PUBLIC_KEYS_PEM = [
    p for p in os.environ.get("IDENTITY_JWT_PREVIOUS_PUBLIC_KEYS_PEM", "").split("|") if p
]
# Networks allowed to reach the Django admin (internal network only).
IDENTITY_ADMIN_ALLOWED_NETWORKS = [
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["services.identity.iam.permissions.InternalServiceToken"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"
