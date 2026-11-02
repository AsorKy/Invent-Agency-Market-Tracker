"""The core company list reads one tenant (REQ-SEC-005).

Row level security on ``core.company`` hides every row unless ``app.tenant_id`` is set, so the
route must pass the ``X-Tenant-Id`` header to the repository. The route is checked with a fake
repository; the SQL and the tenant context are checked in tests/unit/test_cov_infra.py.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from services.core.app import main  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

TENANT = uuid4()


class Repo:
    def __init__(self) -> None:
        self.tenants: list[UUID | None] = []

    async def list_companies(
        self, limit: int, cursor: str | None = None, *, tenant_id: UUID | None = None
    ) -> Any:
        self.tenants.append(tenant_id)
        return [], None


@pytest.mark.parametrize("service", ["gateway", "seed"])
def test_company_list_passes_the_tenant_header(
    monkeypatch: pytest.MonkeyPatch, service: str
) -> None:
    repo = Repo()
    monkeypatch.setattr(main.app.state, "repository", repo, raising=False)
    client = TestClient(main.app)
    headers = service_headers(service, **{"X-Tenant-Id": str(TENANT)})
    response = client.get("/internal/core/companies", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"data": [], "next_cursor": None}
    assert (
        client.get("/internal/core/companies", headers=service_headers(service)).status_code == 200
    )
    assert repo.tenants == [TENANT, None]


def test_company_list_rejects_a_tenant_that_is_not_a_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.app.state, "repository", Repo(), raising=False)
    headers = service_headers(**{"X-Tenant-Id": "not-a-uuid"})
    assert TestClient(main.app).get("/internal/core/companies", headers=headers).status_code == 422
