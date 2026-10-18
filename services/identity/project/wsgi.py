"""WSGI entry point for the identity service (Gunicorn sync workers)."""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "services.identity.project.settings")
application = get_wsgi_application()
