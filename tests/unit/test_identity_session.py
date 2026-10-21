"""Unit tests for identity sessions, cookies, logout, and dev users (REQ-IDN-002).

Uses ``project.settings_test`` (SQLite) and the Django test client. Time is
moved by patching ``django.utils.timezone.now``.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import os
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace

import pytest

os.environ["DJANGO_SETTINGS_MODULE"] = "services.identity.project.settings_test"

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from django.core.management.base import CommandError  # noqa: E402
from django.db import connection  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402

from services.identity.iam.models import (  # noqa: E402
    AuthSession,
    Credential,
    MembershipScope,
    Tenant,
    TenantMembership,
    UserAccount,
)
from services.identity.iam.session_views import set_password  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

MODELS = (Tenant, UserAccount, TenantMembership, MembershipScope, Credential, AuthSession)
TOKEN = {"HTTP_X_SERVICE_TOKEN": service_headers("gateway")["X-Service-Token"]}
LOGIN = "/internal/identity/auth/session"
ME = "/internal/identity/me"
PASSWORD = "unit-test-password-1"  # generated test value, not a credential


@pytest.fixture
def db() -> Iterator[None]:
    with connection.schema_editor() as editor:
        for model in MODELS:
            editor.create_model(model)
    yield
    with connection.schema_editor() as editor:
        for model in reversed(MODELS):
            editor.delete_model(model)


@pytest.fixture
def user(db: None) -> UserAccount:
    tenant = Tenant.objects.create(slug="acme", display_name="Acme")
    account = UserAccount.objects.create(email="Ana@Example.com")
    TenantMembership.objects.create(tenant=tenant, user=account, role="strategist")
    set_password(account.id, PASSWORD)
    return account


def _login(client: Client, password: str = PASSWORD) -> object:
    return client.post(
        LOGIN,
        {"email": "ana@example.com", "password": password},
        content_type="application/json",
        **TOKEN,
    )


def _clock(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    base = timezone.now()
    offset = {"v": timedelta(0)}
    monkeypatch.setattr(timezone, "now", lambda: base + offset["v"])
    return [offset]


def test_production_cookie_attributes(user: UserAccount) -> None:
    with override_settings(IDENTITY_PROFILE="production"):
        resp = _login(Client())
    assert resp.status_code == 200
    session, csrf = resp.cookies["__Host-session"], resp.cookies["__Host-csrf"]
    for cookie in (session, csrf):
        assert cookie["secure"] is True
        assert cookie["samesite"] == "Strict"
        assert cookie["path"] == "/" and not cookie["domain"]
    assert session["httponly"] is True
    assert not csrf["httponly"]  # double-submit token must be readable by the app
    assert 0 < int(session["max-age"]) <= 1800
    assert "token" not in resp.json() and "access_token" not in resp.json()
    assert resp.json()["role"] == "strategist"


def test_local_profile_drops_prefix_and_secure(user: UserAccount) -> None:
    with override_settings(IDENTITY_PROFILE="local"):
        resp = _login(Client())
    assert "session" in resp.cookies and "csrf" in resp.cookies
    assert not resp.cookies["session"]["secure"]
    assert resp.cookies["session"]["httponly"] is True
    assert resp.cookies["session"]["samesite"] == "Strict"


def test_login_rejects_bad_credentials(user: UserAccount) -> None:
    client = Client()
    assert _login(client, "wrong").status_code == 401
    unknown = client.post(LOGIN, {"email": "x@y.z", "password": PASSWORD}, **TOKEN)
    assert unknown.status_code == 401
    assert client.post(LOGIN, {"email": "ana@example.com", "password": PASSWORD}).status_code == 403
    assert client.get(ME, **TOKEN).status_code == 401


def test_session_stores_only_a_hash(user: UserAccount) -> None:
    client = Client()
    raw = _login(client).cookies["session"].value
    stored = AuthSession.objects.get()
    assert stored.key_hash != raw and raw not in stored.key_hash
    assert Credential.objects.get().password_hash != PASSWORD


def test_me_and_validate_use_cookie(user: UserAccount) -> None:
    client = Client()
    _login(client)
    me = client.get(ME, **TOKEN)
    assert me.status_code == 200 and me.json()["email"] == "Ana@Example.com"
    assert "lead.approve" in me.json()["permissions"]
    token = client.get("/internal/identity/auth/validate", **TOKEN).json()["access_token"]
    assert token.count(".") == 2


def test_sliding_renewal_and_idle_expiry(
    user: UserAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    (offset,) = _clock(monkeypatch)
    client = Client()
    _login(client)
    for _ in range(3):  # activity every 20 minutes keeps the session alive
        offset["v"] += timedelta(minutes=20)
        assert client.get(ME, **TOKEN).status_code == 200
    offset["v"] += timedelta(minutes=31)  # idle too long
    assert client.get(ME, **TOKEN).status_code == 401


def test_absolute_expiry_after_twelve_hours(
    user: UserAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    (offset,) = _clock(monkeypatch)
    client = Client()
    _login(client)
    for _ in range(30):  # active every 29 minutes, still capped at 12 hours
        offset["v"] += timedelta(minutes=29)
        if client.get(ME, **TOKEN).status_code != 200:
            break
    assert offset["v"] <= timedelta(hours=12, minutes=29)
    assert client.get(ME, **TOKEN).status_code == 401
    assert AuthSession.objects.get().revoked_at is not None


def test_logout_invalidates_session_and_clears_cookies(user: UserAccount) -> None:
    client = Client()
    raw = _login(client).cookies["session"].value
    out = client.post("/internal/identity/logout", **TOKEN)
    assert out.status_code == 204
    assert out.cookies["session"]["max-age"] == 0
    replay = Client()
    replay.cookies["session"] = raw
    assert replay.get(ME, **TOKEN).status_code == 401


def test_dev_users_from_env_only(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(
        app_profile="local",
        dev_user_password_admin="generated-admin-pw",
        dev_user_password_strategist=None,
        dev_user_password_intake="generated-intake-pw",
        dev_user_password_bd_rep=None,
        dev_user_password_analyst=None,
    )
    target = "services.identity.iam.management.commands.create_dev_users.get_settings"
    monkeypatch.setattr(target, lambda: fake)
    call_command("create_dev_users")
    call_command("create_dev_users")  # idempotent
    roles = set(TenantMembership.objects.values_list("role", flat=True))
    assert roles == {"admin", "intake"}
    assert Credential.objects.count() == 2
    client = Client()
    ok = client.post(LOGIN, {"email": "admin@dev.local", "password": "generated-admin-pw"}, **TOKEN)
    assert ok.status_code == 200 and ok.json()["role"] == "admin"

    fake.app_profile = "production"
    with pytest.raises(CommandError):
        call_command("create_dev_users")
