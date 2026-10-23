"""Unit tests for the identity tenant list for the core service (D-36, T-194).

Uses ``services.identity.project.settings_test`` (SQLite) and the Django test client. Relates to
services/identity/iam/views.py and services/identity/iam/permissions.py.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import os
from collections.abc import Iterator

import pytest

os.environ["DJANGO_SETTINGS_MODULE"] = "services.identity.project.settings_test"

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from django.test import Client  # noqa: E402

from services.identity.iam.models import Tenant  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

ROUTE = "/internal/identity/tenants"


@pytest.fixture
def db() -> Iterator[None]:
    with connection.schema_editor() as editor:
        editor.create_model(Tenant)
    yield
    with connection.schema_editor() as editor:
        editor.delete_model(Tenant)


def _get(service: str | None) -> object:
    extra = (
        {}
        if service is None
        else {"HTTP_X_SERVICE_TOKEN": service_headers(service)["X-Service-Token"]}
    )
    return Client().get(ROUTE, **extra)


def test_core_gets_only_active_tenant_ids(db: None) -> None:
    active = Tenant.objects.create(slug="acme", display_name="Acme Secret Name")
    Tenant.objects.create(slug="old", display_name="Old", status="suspended")
    response = _get("core")
    assert response.status_code == 200
    body = response.json()
    assert body == {"tenant_ids": [str(active.id)]}
    assert "Acme" not in response.content.decode()
    assert "acme" not in response.content.decode()


def test_empty_list_when_no_tenants(db: None) -> None:
    assert _get("core").json() == {"tenant_ids": []}


@pytest.mark.parametrize("service", ["gateway", "seed", "verify_live", None])
def test_other_callers_and_missing_token_get_401(service: str | None, db: None) -> None:
    assert _get(service).status_code == 401


def test_raw_or_garbage_token_is_refused(db: None) -> None:
    response = Client().get(ROUTE, HTTP_X_SERVICE_TOKEN="not-a-token")
    assert response.status_code == 401


def test_write_methods_are_not_allowed(db: None) -> None:
    token = {"HTTP_X_SERVICE_TOKEN": service_headers("core")["X-Service-Token"]}
    assert Client().post(ROUTE, **token).status_code == 405
