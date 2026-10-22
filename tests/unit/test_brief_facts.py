"""Contract tests for the core brief-facts endpoint and the kanban ``as_of`` (REQ-BRF-001).

The route runs against a fake repository; the response is validated with the
``CompanyFacts`` model that ``services/brief`` uses, so the two services cannot drift.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from services.brief.app.models import CompanyFacts  # noqa: E402
from services.core.app import main  # noqa: E402
from services.core.app.brief_facts import build_facts  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

TENANT = uuid4()
COMPANY = uuid4()
NOW = datetime(2026, 10, 19, tzinfo=UTC)


def _facts() -> dict[str, object]:
    return build_facts(
        {
            "version": 3,
            "nit": "900123456",
            "sector_code": "62",
            "city": "Cali",
            "score": 82,
            "source_flags": {"enrichment_flags": ["no_website"], "rues_attempts": 2},
        },
        ["funding", "hiring"],
        5,
        [{"activity_type": "call", "occurred_at": NOW - timedelta(days=4)}],
        NOW,
    )


class Repo:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID | None]] = []

    async def brief_facts(self, company_id: UUID, tenant_id: UUID | None):
        self.calls.append((company_id, tenant_id))
        return _facts() if tenant_id == TENANT and company_id == COMPANY else None

    async def list_leads(self, *args, **kwargs):
        return [], None


@pytest.fixture
def client():
    main.app.state.repository = Repo()
    try:
        yield TestClient(main.app, headers=service_headers())
    finally:
        main.app.dependency_overrides.clear()


def test_response_validates_against_brief_model(client) -> None:
    response = client.get(
        f"/internal/core/companies/{COMPANY}/brief-facts", headers={"X-Tenant-Id": str(TENANT)}
    )
    assert response.status_code == 200
    facts = CompanyFacts(company_id=COMPANY, **response.json())
    assert facts.expected_version == 8
    assert facts.score == 82
    assert facts.rues_status == "matched"
    assert facts.rues_attempts == 2
    assert facts.days_since_contact == 4
    assert facts.signal_codes == ["funding", "hiring"]
    assert facts.enrichment_flags == ["no_website"]
    assert facts.activity[0]["type"] == "call"


def test_other_tenant_and_unknown_company_are_404(client) -> None:
    url = f"/internal/core/companies/{COMPANY}/brief-facts"
    assert client.get(url, headers={"X-Tenant-Id": str(uuid4())}).status_code == 404
    assert (
        client.get(
            f"/internal/core/companies/{uuid4()}/brief-facts",
            headers={"X-Tenant-Id": str(TENANT)},
        ).status_code
        == 404
    )


def test_quarantine_days_and_defaults() -> None:
    facts = build_facts(
        {
            "version": 1,
            "nit": None,
            "source_flags": {
                "rues_status": "quarantined",
                "quarantined_at": (NOW - timedelta(days=9)).isoformat(),
                "domain_certificate_ok": False,
                "enrichment_failed": True,
            },
        },
        [],
        0,
        [],
        NOW,
    )
    model = CompanyFacts(company_id=COMPANY, **facts)
    assert model.quarantine_days == 9
    assert not model.domain_certificate_ok
    assert model.enrichment_failed
    assert model.days_since_contact is None
    pending = build_facts({"version": 1, "nit": None, "source_flags": {}}, [], 0, [], NOW)
    assert pending["rues_status"] == "pending"


def test_kanban_as_of_is_real_timestamp(client) -> None:
    body = client.get("/internal/core/pipeline/kanban").json()
    assert isinstance(body["as_of"], str)
    assert datetime.fromisoformat(body["as_of"]).tzinfo is not None


def test_core_sql_never_reads_the_ml_schema() -> None:
    """INV-01: ``core_app`` has no grant on ``ml``, so a JOIN there answers 500 in a real stack."""
    from pathlib import Path

    for name in ("brief_facts.py", "repository.py"):
        source = (Path(__file__).resolve().parents[2] / "services/core/app" / name).read_text()
        assert "ml.lead_score" not in source, name
