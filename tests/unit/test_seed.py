"""Tests for the demo seed (REQ-RUN-003) with an in-memory core and no network."""

from typing import Any
from uuid import uuid4

from libs.common.settings import get_settings
from ops.seed import __main__ as seed_cli
from ops.seed import loader
from services.core.app.companies import CompanyUpsert, company_slug

SMALL = {"orgs": 6, "days": 30, "seed": 7}


class FakeCore:
    """Mimics core: slug-keyed company upsert, deduplicated signals, opt-out."""

    def __init__(self) -> None:
        self.companies: dict[str, dict[str, Any]] = {}
        self.signals: dict[tuple[str, str], dict[str, Any]] = {}
        self.foreign_id = str(uuid4())
        self.companies["ck-foreign"] = {"id": self.foreign_id, "slug": "ck-foreign"}

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]:
        slug = company_slug(CompanyUpsert(**body))
        row = self.companies.setdefault(slug, {"id": str(uuid4()), "slug": slug})
        row.update(body)
        return {"company_id": row["id"], "status": "created"}

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]:
        key = (body["source"], body["source_external_id"])
        self.signals.setdefault(key, body | {"company_id": body["company_id"]})
        return {"signal_id": str(uuid4()), "status": "created"}

    async def list_companies(
        self, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        rows = list(self.companies.values())
        start = int(cursor or 0)
        page = rows[start : start + limit]
        more = start + limit < len(rows)
        return page, str(start + limit) if more else None

    async def opt_out(self, company_id: str) -> dict[str, Any]:
        for slug, row in list(self.companies.items()):
            if row["id"] == company_id:
                del self.companies[slug]
                self.signals = {
                    k: v for k, v in self.signals.items() if v["company_id"] != company_id
                }
                return {"status": "removed"}
        return {"status": "already_removed"}


async def test_load_tags_everything_as_demo() -> None:
    core = FakeCore()
    result = await loader.load(core, **SMALL)
    assert result.delivered > 0 and result.failed == 0
    assert core.signals
    assert {source for source, _ in core.signals} == {"demo"}
    demo = [r for r in core.companies.values() if r["slug"] != "ck-foreign"]
    assert len(demo) == SMALL["orgs"]
    assert all(r["source_ids"] == {"demo": "demo"} for r in demo)
    origins = {s["payload"]["origin_source"] for s in core.signals.values()}
    assert origins == {"maps", "computrabajo"}


async def test_load_is_idempotent() -> None:
    core = FakeCore()
    await loader.load(core, **SMALL)
    companies, signals = len(core.companies), len(core.signals)
    await loader.load(core, **SMALL)
    assert (len(core.companies), len(core.signals)) == (companies, signals)


async def test_clear_removes_only_demo_rows() -> None:
    core = FakeCore()
    await loader.load(core, **SMALL)
    result = await loader.clear(core, page_size=2, **SMALL)
    assert result.removed == SMALL["orgs"]
    assert list(core.companies) == ["ck-foreign"]
    assert core.signals == {}
    again = await loader.clear(core, **SMALL)
    assert again.removed == 0


async def test_demo_slugs_match_loaded_companies() -> None:
    core = FakeCore()
    await loader.load(core, **SMALL)
    slugs = await loader.demo_slugs(**SMALL)
    assert slugs == set(core.companies) - {"ck-foreign"}


async def test_cli_runs_with_injected_core(capsys: Any) -> None:
    get_settings.cache_clear()
    core = FakeCore()
    args = ["--orgs", "4", "--days", "10", "--seed", "3"]
    assert await seed_cli.run(["load", *args], core=core) == 0
    assert "seed load: delivered=" in capsys.readouterr().out
    assert await seed_cli.run(["clear", *args], core=core) == 0
    assert "removed=4" in capsys.readouterr().out
