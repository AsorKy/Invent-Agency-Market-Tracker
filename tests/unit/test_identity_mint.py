"""Unit tests for the dedicated socioeconomic mint route of identity (D-33, T-185).

Uses ``services.identity.project.settings_test`` and the Django test client. Relates to
services/identity/iam/views.py and libs/common/internal_auth.py.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import logging
import os
import time

import pytest

os.environ["DJANGO_SETTINGS_MODULE"] = "services.identity.project.settings_test"

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

from libs.common.internal_auth import verify_service_token  # noqa: E402
from libs.common.settings import get_settings  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

ROUTE = "/internal/identity/mint/socioeco"
SHARED_ROUTE = "/internal/identity/service-token"
CREDENTIAL = "unit-test-mint-credential-value"
HEADER = "HTTP_X_MINT_CREDENTIAL"


@pytest.fixture(autouse=True)
def credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("N8N_SOCIOECO_MINT_CREDENTIAL", CREDENTIAL)


def test_dedicated_credential_mints_only_the_allowed_token() -> None:
    response = Client().post(ROUTE, **{HEADER: CREDENTIAL})
    assert response.status_code == 200
    token = response.json()["access_token"]
    secret = get_settings().internal_service_token
    assert verify_service_token(token, secret) == "n8n_socioeco"
    expires = int(token.split(".")[2])
    assert 0 < expires - time.time() <= 61


def test_body_cannot_choose_the_service() -> None:
    response = Client().post(
        ROUTE, {"service": "gateway"}, content_type="application/json", **{HEADER: CREDENTIAL}
    )
    assert response.status_code == 200
    secret = get_settings().internal_service_token
    assert verify_service_token(response.json()["access_token"], secret) == "n8n_socioeco"


@pytest.mark.parametrize("value", [None, "", "wrong", CREDENTIAL + "x", CREDENTIAL[:-1]])
def test_wrong_or_missing_credential_is_refused(value: str | None) -> None:
    extra = {} if value is None else {HEADER: value}
    response = Client().post(ROUTE, **extra)
    assert response.status_code == 401


def test_unset_or_empty_credential_always_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("N8N_SOCIOECO_MINT_CREDENTIAL", "")
    assert Client().post(ROUTE, **{HEADER: ""}).status_code == 401
    monkeypatch.delenv("N8N_SOCIOECO_MINT_CREDENTIAL")
    assert Client().post(ROUTE, **{HEADER: "anything"}).status_code == 401


def test_shared_key_route_does_not_accept_the_dedicated_credential() -> None:
    response = Client().post(SHARED_ROUTE, {"service": "gateway"}, **{HEADER: CREDENTIAL})
    assert response.status_code in (401, 403)
    as_token = Client().post(
        SHARED_ROUTE,
        {"service": "gateway"},
        HTTP_X_SERVICE_TOKEN=CREDENTIAL,
    )
    assert as_token.status_code in (401, 403)


def test_mint_route_does_not_accept_a_shared_key_token() -> None:
    headers = {"HTTP_X_SERVICE_TOKEN": service_headers("gateway")["X-Service-Token"]}
    assert Client().post(ROUTE, **headers).status_code == 401


def test_no_response_or_log_contains_the_secret(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    bodies = [
        Client().post(ROUTE, **{HEADER: CREDENTIAL}),
        Client().post(ROUTE, **{HEADER: "wrong"}),
        Client().post(ROUTE),
    ]
    for response in bodies:
        assert CREDENTIAL not in response.content.decode()
        assert CREDENTIAL not in str(response.headers)
    assert CREDENTIAL not in caplog.text
    audit = [r for r in caplog.records if r.name == "imt.audit"]
    assert len(audit) == 1
    assert "n8n_socioeco" in audit[0].getMessage()
