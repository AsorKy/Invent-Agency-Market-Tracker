"""Unit tests for attribution storage (T-136, REQ-LNS-005).

Covers the migration text, the ml publisher with an injected fake client, and
the Pulse ingest route with an in-memory fake store. No database or network.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")
testclient = pytest.importorskip("fastapi.testclient")

from libs.common.internal_auth import verify_service_token  # noqa: E402
from libs.common.settings import get_settings  # noqa: E402
from libs.domain.attribution import Journey, Touch  # noqa: E402
from services.ml.app.attribution_service import (  # noqa: E402
    ATTRIBUTION_PATH,
    attribute,
    publish_attribution,
)
from services.pulse.app import main as pulse  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
PROJECT = "33333333-3333-3333-3333-333333333333"
MIGRATION = Path("migrations/20261021_0011_pulse_attribution.sql")


def _journeys() -> list[Journey]:
    def at(day: int) -> datetime:
        return datetime(2026, 1, day, tzinfo=UTC)

    return [
        Journey((Touch("web", at(1)), Touch("mail", at(2))), True),
        Journey((Touch("mail", at(1)),), True),
        Journey((Touch("web", at(1)),), False),
    ]


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, str]]] = []

    async def post(self, url: str, *, json: Any, headers: dict[str, str]) -> FakeResponse:
        self.calls.append((url, json, headers))
        return FakeResponse()


class FakeStore:
    def __init__(self) -> None:
        self.saved: list[tuple[UUID, Any]] = []

    async def save_attribution(self, tenant: UUID, body: Any) -> int:
        self.saved.append((tenant, body))
        return len(body.rows)


def test_migration_is_tenant_scoped_and_uses_numeric() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "tenant_id uuid NOT NULL" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "app.tenant_id" in sql
    assert "credit numeric" in sql
    assert "double" not in sql.lower()
    assert "DROP " not in sql.upper()


def test_publish_sends_signed_ml_token_and_tenant() -> None:
    rows = attribute(_journeys(), "last_touch")
    assert [(r["channel"], r["credit"]) for r in rows] == [("mail", 2.0)]
    client = FakeClient()
    secret = get_settings().internal_service_token
    asyncio.run(
        publish_attribution(
            client,
            secret,
            tenant_id=UUID(TENANT),
            project_id=UUID(PROJECT),
            source_version="v1",
            rows=rows,
        )
    )
    ((url, body, headers),) = client.calls
    assert url == ATTRIBUTION_PATH
    assert verify_service_token(headers["X-Service-Token"], secret) == "ml"
    assert body == {
        "tenant_id": TENANT,
        "project_id": PROJECT,
        "source_version": "v1",
        "rows": [{"model": "last_touch", "channel": "mail", "credit": 2.0}],
    }


def test_first_touch_hand_computed() -> None:
    rows = attribute(_journeys(), "first_touch")
    assert [(r["channel"], r["credit"]) for r in rows] == [("mail", 1.0), ("web", 1.0)]


@pytest.fixture
def store() -> FakeStore:
    fake = FakeStore()
    pulse.app.state.lens_store = fake
    return fake


def _body(**extra: Any) -> dict[str, Any]:
    rows = attribute(_journeys(), "first_touch")
    return {
        "project_id": PROJECT,
        "source_version": "v1",
        "rows": [{k: r[k] for k in ("model", "channel", "credit")} for r in rows],
        **extra,
    }


def test_route_stores_rows_for_body_tenant(store: FakeStore) -> None:
    client = testclient.TestClient(pulse.app)
    response = client.post(
        ATTRIBUTION_PATH, json=_body(tenant_id=TENANT), headers=service_headers("ml")
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"project_id": PROJECT, "accepted": 2}
    assert str(store.saved[0][0]) == TENANT


def test_route_refuses_missing_mismatched_tenant_and_other_services(store: FakeStore) -> None:
    client = testclient.TestClient(pulse.app)
    missing = client.post(ATTRIBUTION_PATH, json=_body(), headers=service_headers("ml"))
    assert missing.status_code == 400 and missing.json()["detail"] == "tenant_required"
    mismatch = client.post(
        ATTRIBUTION_PATH,
        json=_body(tenant_id=OTHER),
        headers=service_headers("ml", **{"X-Tenant-Id": TENANT}),
    )
    assert mismatch.json()["detail"] == "tenant_mismatch"
    denied = client.post(
        ATTRIBUTION_PATH, json=_body(tenant_id=TENANT), headers=service_headers("gateway")
    )
    assert denied.status_code == 401
    bad_model = _body(tenant_id=TENANT)
    bad_model["rows"][0]["model"] = "bogus"
    assert (
        client.post(ATTRIBUTION_PATH, json=bad_model, headers=service_headers("ml")).status_code
        == 422
    )
    assert store.saved == []
