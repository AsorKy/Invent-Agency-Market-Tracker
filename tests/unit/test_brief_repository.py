"""Unit tests for the Brief SQL repository using fake engine and connection objects.

The fakes emulate ``brief.record_digest`` in memory (no database), record the
tenant context call, and check the upsert guard, latest-version read, epoch
conversion and the missing-tenant failure (REQ-BRF-001, REQ-BRF-002).
"""

from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from support_brief import COMPANY, TENANT

from services.brief.app.models import BriefContent, StoredDigest
from services.brief.app.repository import (
    BriefRepository,
    SqlBriefRepository,
    TenantRequired,
    create_repository,
)


class _Mappings:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_Mappings":
        return self

    def first(self) -> dict | None:
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self, store: dict, log: list) -> None:
        self.store = store
        self.log = log

    async def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "set_config" in sql:
            self.log.append(("tenant", params["tenant_id"]))
            return _Mappings([])
        if "ORDER BY source_version DESC" in sql:
            rows = [r for (c, _), r in self.store.items() if c == params["company_id"]]
            rows.sort(key=lambda r: r["source_version"], reverse=True)
            return _Mappings(rows[:1])
        assert "ON CONFLICT (company_id, source_version) DO UPDATE" in sql
        key = (params["company_id"], params["source_version"])
        if any(c == key[0] and v > key[1] for (c, v) in self.store):
            return _Mappings([])
        self.store[key] = {
            "company_id": params["company_id"],
            "source_version": params["source_version"],
            "content": params["content"],
            "generated_at": params["generated_at"],
            "summarizer_version": params["summarizer_version"],
            "degraded": params["degraded"],
            "config_version": params["config_version"],
        }
        return _Mappings([])


class FakeEngine:
    def __init__(self) -> None:
        self.store: dict = {}
        self.log: list = []
        self.disposed = False

    @asynccontextmanager
    async def begin(self):
        yield FakeConnection(self.store, self.log)
        self.log.append(("commit", None))

    async def dispose(self) -> None:
        self.disposed = True


def _digest(
    version: int, *, headline: str = "h", generated_at: int = 1_700_000_000_123
) -> StoredDigest:
    return StoredDigest(
        company_id=COMPANY,
        source_version=version,
        content=BriefContent(
            headline=headline, alerts=[], next_steps=[], coverage={"rues": True}, activity=[]
        ),
        generated_at=generated_at,
        summarizer_version="rule-v1",
        degraded=True,
        config_version=3,
    )


def _repo() -> tuple[SqlBriefRepository, FakeEngine]:
    engine = FakeEngine()
    return SqlBriefRepository(engine), engine  # type: ignore[arg-type]


async def test_round_trip_converts_epoch_and_sets_tenant_first() -> None:
    repo, engine = _repo()
    await repo.save(_digest(2), TENANT)
    assert engine.log[0] == ("tenant", str(TENANT))
    stored = next(iter(engine.store.values()))
    assert isinstance(stored["generated_at"], datetime)
    assert stored["generated_at"].tzinfo is not None
    latest = await repo.get_latest(COMPANY, TENANT)
    assert latest == _digest(2)


async def test_get_latest_returns_highest_version_and_none_when_empty() -> None:
    repo, _ = _repo()
    assert await repo.get_latest(COMPANY, TENANT) is None
    await repo.save(_digest(1), TENANT)
    await repo.save(_digest(3), TENANT)
    await repo.save(_digest(2), TENANT)
    latest = await repo.get_latest(COMPANY, TENANT)
    assert latest is not None and latest.source_version == 3


async def test_save_same_version_replaces_and_older_is_ignored() -> None:
    repo, _ = _repo()
    await repo.save(_digest(2, headline="old"), TENANT)
    await repo.save(_digest(2, headline="new"), TENANT)
    await repo.save(_digest(1, headline="stale"), TENANT)
    latest = await repo.get_latest(COMPANY, TENANT)
    assert latest is not None
    assert latest.source_version == 2 and latest.content.headline == "new"


async def test_missing_tenant_is_rejected() -> None:
    repo, engine = _repo()
    with pytest.raises(TenantRequired):
        await repo.get_latest(COMPANY)
    with pytest.raises(TenantRequired):
        await repo.save(_digest(1))
    assert engine.log == []


async def test_close_disposes_engine() -> None:
    repo, engine = _repo()
    await repo.close()
    assert engine.disposed


async def test_in_memory_repository_ignores_tenant_and_factory_falls_back() -> None:
    memory = BriefRepository(None)
    await memory.save(_digest(1), uuid4())
    assert (await memory.get_latest(COMPANY)) == _digest(1)
    assert isinstance(create_repository(None), BriefRepository)
    other: UUID = uuid4()
    assert await memory.get_latest(other, TENANT) is None
