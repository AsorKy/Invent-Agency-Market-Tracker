"""The core score-features read on a real PostgreSQL as ``core_app`` (T-165, REQ-SEC-005).

Two tenants own companies; the read of tenant A never returns tenant B's rows, a read without
tenant context returns none, and the features match the shared derivation. Fixtures are written by
the server user. Skipped unless ``TEST_DATABASE_URL`` is set (a superuser server URL); needs the
``sqltest`` extra.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from support_services import asyncpg_url, migrated_database, needs_database

pytestmark = needs_database

PASSWORD = "imt-score-features-only"
TENANT = UUID("aaaaaaaa-0000-4000-8000-000000000011")
OTHER = UUID("bbbbbbbb-0000-4000-8000-000000000012")


class Db:
    def __init__(self, admin: str) -> None:
        from support_sql_pg import bootstrap_app_roles, role_url

        roles = bootstrap_app_roles(admin, PASSWORD)
        self.admin = admin
        self.core_url = role_url(admin, roles["core"], PASSWORD)

    def rows(self, sql_text: str, *params: Any) -> list[tuple[Any, ...]]:
        from support_sql_pg import admin_execute

        return admin_execute(self.admin, sql_text, params)

    def company(
        self, tenant: UUID, city: str, *, website: str | None = None, nit: str | None = None
    ) -> UUID:
        company_id = uuid4()
        self.rows(
            "INSERT INTO core.company (id, tenant_id, slug, legal_name, city, website, nit) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            company_id,
            tenant,
            f"c-{company_id.hex[:12]}",
            f"Company {company_id.hex[:6]}",
            city,
            website,
            nit,
        )
        return company_id

    def signal(self, tenant: UUID, company: UUID, count: int) -> None:
        for _ in range(count):
            self.rows(
                "INSERT INTO core.signal (company_id, tenant_id, source, signal_type, observed_at)"
                " VALUES (%s, %s, 'test', 'news', now())",
                company,
                tenant,
            )


@pytest.fixture(scope="module")
def db() -> Iterator[Db]:
    with migrated_database() as url:
        yield Db(url)


@pytest.fixture
async def engine(db: Db) -> AsyncIterator[Any]:
    pytest.importorskip("asyncpg")
    from libs.common.db import create_engine

    made = create_engine(asyncpg_url(db.core_url), pool_size=2)
    try:
        yield made
    finally:
        await made.dispose()


async def test_tenant_a_never_sees_tenant_b_and_features_match(db: Db, engine: Any) -> None:
    from services.core.app.score_features import read_score_features

    full = db.company(TENANT, "Cali", website="https://x.co", nit="900")
    bare = db.company(TENANT, "Cali")
    foreign = [db.company(OTHER, "Cali") for _ in range(3)]
    db.signal(TENANT, full, 3)
    db.signal(OTHER, foreign[0], 2)

    body = await read_score_features(engine, TENANT, 500, None)
    by_id = {UUID(c["company_id"]): c for c in body["companies"]}
    assert set(by_id) == {full, bare}
    assert not set(foreign) & set(by_id)
    assert by_id[full]["icp_fit"] == pytest.approx(1.0)
    assert by_id[full]["signal_strength"] == pytest.approx(0.5)
    assert by_id[bare]["icp_fit"] == pytest.approx(0.4)
    assert by_id[bare]["territory_opportunity"] == 1.0  # only tenant A's companies count
    assert body["next_after"] is None

    other = await read_score_features(engine, OTHER, 500, None)
    assert {UUID(c["company_id"]) for c in other["companies"]} == set(foreign)


async def test_paging_by_id(db: Db, engine: Any) -> None:
    from services.core.app.score_features import read_score_features

    tenant = uuid4()
    made = sorted(db.company(tenant, "Pasto") for _ in range(3))
    first = await read_score_features(engine, tenant, 2, None)
    assert [UUID(c["company_id"]) for c in first["companies"]] == made[:2]
    assert first["next_after"] == str(made[1])
    second = await read_score_features(engine, tenant, 2, UUID(first["next_after"]))
    assert [UUID(c["company_id"]) for c in second["companies"]] == made[2:]
    assert second["next_after"] is None


async def test_read_without_tenant_context_returns_none(db: Db, engine: Any) -> None:
    from sqlalchemy import text

    db.company(TENANT, "Cali")
    async with engine.begin() as connection:
        rows = (await connection.execute(text("SELECT id FROM core.company"))).all()
    assert rows == []
