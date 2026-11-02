"""Market insights and the RUES pending label for real Bright Data companies (REQ-STR-001).

``services/core/app/insights.py`` aggregates core's own tables (INV-01) for the Streamlit Market
page; the core company and lead lists carry ``rues_status`` and the lead list carries the
decision makers of the company. Everything runs on scripted fakes, without Postgres.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine  # noqa: E402

from services.core.app import main  # noqa: E402
from services.core.app import repository as repo  # noqa: E402
from services.core.app.insights import (  # noqa: E402
    CITY_CATEGORY_SQL,
    HIRING_SQL,
    MID_SIZE_SQL,
    build_insights,
    read_insights,
)
from tests.unit.service_auth import service_headers  # noqa: E402
from tests.unit.support_cov import FakeEngine, FakeResult  # noqa: E402

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)
TENANT = UUID("00000000-0000-4000-8000-0000000000a1")


def _group(city: str | None, category: str | None, companies: int, **maps: Any) -> dict[str, Any]:
    base = {"listings": 0, "rated": 0, "rating_sum": None, "reviewed": 0, "reviews_sum": None}
    return {"city": city, "category": category, "companies": companies, **base, **maps}


GROUPS = [
    _group(
        "Medellin",
        "restaurante",
        3,
        listings=3,
        rated=2,
        rating_sum=Decimal("9.0"),
        reviewed=3,
        reviews_sum=Decimal("300"),
    ),
    _group("Medellin", "software", 1, listings=1, rated=1, rating_sum=Decimal("4.0")),
    _group("Bogota", "restaurante", 2, listings=2, rated=2, rating_sum=Decimal("8.6")),
    _group("Cali", None, 1),  # a company known only from LinkedIn: no Maps data
]
HIRING = [{"city": "Bogota", "keyword": "desarrollador", "postings": 7, "companies": 3}]
SIZES = [
    {"city": "Medellin", "companies": 4, "with_size": 3, "mid_size": 2},
    {"city": "Bogota", "companies": 2, "with_size": 1, "mid_size": 0},
    {"city": "Cali", "companies": 1, "with_size": 0, "mid_size": 0},
]


def test_build_insights_weights_averages_and_keeps_null_for_no_data() -> None:
    body = build_insights(GROUPS, HIRING, SIZES, NOW)
    assert body["as_of"] == NOW.isoformat() and body["stale"] is False
    assert body["companies"] == 7
    first = body["by_city_category"][0]
    assert first == {
        "city": "Medellin",
        "category": "restaurante",
        "companies": 3,
        "listings": 3,
        "avg_rating": 4.5,
        "avg_reviews": 100.0,
    }
    cali = next(row for row in body["by_city_category"] if row["city"] == "Cali")
    assert (cali["listings"], cali["avg_rating"], cali["avg_reviews"]) == (0, None, None)
    # City averages are weighted by the rated listings, not averages of averages.
    by_city = {row["city"]: row for row in body["maps_by_city"]}
    assert by_city["Medellin"] == {
        "city": "Medellin",
        "listings": 4,
        "avg_rating": 4.33,
        "avg_reviews": 100.0,
    }
    assert by_city["Bogota"]["avg_rating"] == 4.3 and by_city["Bogota"]["avg_reviews"] is None
    assert "Cali" not in by_city  # no listing, no row
    assert body["hiring"] == HIRING
    mid = body["mid_size"]
    assert (mid["min_employees"], mid["max_employees"]) == (11, 500)
    assert (mid["companies"], mid["companies_with_size"], mid["mid_size"]) == (7, 4, 2)
    assert mid["share"] == 0.5
    assert [row["share"] for row in mid["by_city"]] == [0.6667, 0.0, None]


def test_build_insights_renders_empty_data() -> None:
    body = build_insights([], [], [], NOW)
    assert body["companies"] == 0
    assert body["by_city_category"] == body["maps_by_city"] == body["hiring"] == []
    assert body["mid_size"]["share"] is None and body["mid_size"]["by_city"] == []


async def test_read_insights_sets_the_tenant_then_reads_one_tenant() -> None:
    engine = FakeEngine([FakeResult(), FakeResult(GROUPS), FakeResult(HIRING), FakeResult(SIZES)])
    body = await read_insights(cast(AsyncEngine, engine), TENANT)
    assert body["companies"] == 7
    context, *reads = engine.connection.calls
    assert "app.tenant_id" in context[0] and context[1] == {"tenant_id": str(TENANT)}
    assert [sql for sql, _ in reads] == [
        " ".join(s.split()) for s in (CITY_CATEGORY_SQL, HIRING_SQL, MID_SIZE_SQL)
    ]
    assert all(params["tenant_id"] == TENANT for _, params in reads)
    assert reads[2][1] | {} == {"tenant_id": TENANT, "min_employees": 11, "max_employees": 500}
    assert engine.log == ["enter:begin", "exit:begin:ok"]


async def test_read_insights_without_tenant_reads_nothing() -> None:
    engine = FakeEngine()
    body = await read_insights(cast(AsyncEngine, engine), None)
    assert engine.connection.calls == [] and body["companies"] == 0


def test_contract_sources_and_fields_are_the_ones_read() -> None:
    # Source names and Maps fields of the importer contract.
    assert "s.source = 'maps'" in CITY_CATEGORY_SQL
    for field in ("category", "rating", "reviews_count"):
        assert f"'{field}'" in CITY_CATEGORY_SQL
    assert "s.source = 'linkedin_jobs'" in HIRING_SQL
    assert "employee_count BETWEEN :min_employees AND :max_employees" in MID_SIZE_SQL


class InsightsRepo:
    def __init__(self) -> None:
        self.tenants: list[UUID | None] = []

    async def insights(self, tenant_id: UUID | None) -> dict[str, Any]:
        self.tenants.append(tenant_id)
        return build_insights([], [], [], NOW)


def test_insights_route_passes_the_tenant_and_is_gateway_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = InsightsRepo()
    monkeypatch.setattr(main.app.state, "repository", fake, raising=False)
    client = TestClient(main.app)
    path = "/internal/core/companies/insights"
    response = client.get(path, headers=service_headers(**{"X-Tenant-Id": str(TENANT)}))
    assert response.status_code == 200 and response.json()["hiring"] == []
    assert fake.tenants == [TENANT]
    # Only the gateway, which derives the tenant from verified claims, may call it.
    assert client.get(path, headers=service_headers("seed")).status_code == 401


def _repository(results: list[FakeResult]) -> tuple[repo.CoreRepository, FakeEngine]:
    repository = repo.CoreRepository(None)
    engine = FakeEngine(results)
    repository.engine = cast(AsyncEngine, engine)
    return repository, engine


async def test_company_list_reports_rues_pending_without_a_nit() -> None:
    row = {"id": uuid4(), "legal_name": "Logistica Andina", "rues_status": "pending"}
    repository, engine = _repository([FakeResult(), FakeResult([row])])
    page, _ = await repository.list_companies(5, tenant_id=TENANT)
    assert page == [row]
    sql = engine.connection.calls[1][0]
    assert "WHEN nit IS NOT NULL AND nit <> '' THEN 'matched' ELSE 'pending' END" in sql
    assert "AS rues_status" in sql


async def test_lead_list_carries_rues_status_and_public_decision_maker_fields() -> None:
    repository, engine = _repository([FakeResult(), FakeResult([])])
    await repository.list_leads(5, tenant_id=TENANT)
    sql = engine.connection.calls[1][0]
    assert "AS rues_status" in sql and "AS decision_makers" in sql
    assert "COALESCE(l.city, c.city) AS city" in sql
    assert "s.signal_type = 'decision_maker'" in sql
    # Only name, title and public profile (INV-12).
    assert "'name', d.payload->>'name', 'title', d.payload->>'title'" in sql
    assert "'profile_url', d.payload->>'profile_url'" in sql
    assert "found_at" not in sql
