"""Provide the Brief digest persistence seam.

``BriefStore`` is the interface the service depends on. ``BriefRepository`` is
the in-memory implementation kept for tests and local mode (no database URL).
``SqlBriefRepository`` persists to ``brief.record_digest`` (migrations 0002 and
0007) with the same latest-by-company behavior (REQ-BRF-001, REQ-BRF-002).

Tenant handling: ``StoredDigest`` carries no tenant, and row level security on
``brief.record_digest`` filters by ``app.tenant_id``. The tenant is therefore an
explicit per-call argument (``tenant_id``) that the service forwards from the
request, because one Brief process serves many tenants. The SQL repository
opens one transaction per call, runs ``set_tenant_context`` first, and raises
``TenantRequired`` when no tenant is given. The in-memory repository ignores it.
``brief.single_flight_lock`` and ``brief.processed_event`` need no SQL version:
locking uses Redis (``single_flight.py``) and the service consumes no events.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from libs.common.db import create_engine
from libs.common.tenant import set_tenant_context
from services.brief.app.models import BriefContent, StoredDigest


class TenantRequired(Exception):
    """Raised when the SQL repository is used without a tenant id."""


class BriefStore(Protocol):
    """Persistence interface used by ``BriefService``."""

    async def close(self) -> None: ...

    async def get_latest(
        self, company_id: UUID, tenant_id: UUID | None = None
    ) -> StoredDigest | None: ...

    async def save(self, digest: StoredDigest, tenant_id: UUID | None = None) -> None: ...


class BriefRepository:
    """Store the latest digest per company for the service shell."""

    def __init__(self, database_url: str | None) -> None:
        self.database_url = database_url
        self._records: dict[UUID, StoredDigest] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        """Release repository resources.

        The shell has no external persistence yet, so shutdown is a no-op.
        """

    async def get_latest(
        self, company_id: UUID, tenant_id: UUID | None = None
    ) -> StoredDigest | None:
        """Return the most recent digest for a company, if one exists."""
        async with self._lock:
            return self._records.get(company_id)

    async def save(self, digest: StoredDigest, tenant_id: UUID | None = None) -> None:
        """Persist a digest when it is newer than the current record."""
        async with self._lock:
            current = self._records.get(digest.company_id)
            if current is None or digest.source_version >= current.source_version:
                self._records[digest.company_id] = digest


_SELECT_LATEST = text(
    """
    SELECT company_id, source_version, content, generated_at, summarizer_version,
           degraded, config_version
    FROM brief.record_digest
    WHERE company_id = :company_id
    ORDER BY source_version DESC
    LIMIT 1
    """
)

# One statement keeps the "never overwrite a newer version" rule atomic.
_UPSERT = text(
    """
    INSERT INTO brief.record_digest
        (tenant_id, company_id, source_version, content, generated_at,
         summarizer_version, degraded, config_version)
    SELECT :tenant_id, :company_id, :source_version, CAST(:content AS jsonb),
           :generated_at, :summarizer_version, :degraded, :config_version
    WHERE NOT EXISTS (
        SELECT 1 FROM brief.record_digest
        WHERE company_id = :company_id AND source_version > :source_version
    )
    ON CONFLICT (company_id, source_version) DO UPDATE
    SET content = EXCLUDED.content,
        generated_at = EXCLUDED.generated_at,
        summarizer_version = EXCLUDED.summarizer_version,
        degraded = EXCLUDED.degraded,
        config_version = EXCLUDED.config_version
    """
)


class SqlBriefRepository:
    """Store digests in ``brief.record_digest`` under tenant row level security."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def close(self) -> None:
        """Dispose the connection pool."""
        await self._engine.dispose()

    async def get_latest(
        self, company_id: UUID, tenant_id: UUID | None = None
    ) -> StoredDigest | None:
        """Return the highest ``source_version`` digest for the company."""
        if tenant_id is None:
            raise TenantRequired("brief SQL repository needs a tenant id")
        async with self._engine.begin() as connection:
            await set_tenant_context(connection, tenant_id)
            result = await connection.execute(_SELECT_LATEST, {"company_id": company_id})
            row = result.mappings().first()
        if row is None:
            return None
        content = row["content"]
        if isinstance(content, str | bytes):
            content = json.loads(content)
        generated_at: datetime = row["generated_at"]
        return StoredDigest(
            company_id=row["company_id"],
            source_version=row["source_version"],
            content=BriefContent.model_validate(content),
            generated_at=int(generated_at.timestamp() * 1000),
            summarizer_version=row["summarizer_version"],
            degraded=row["degraded"],
            config_version=row["config_version"],
        )

    async def save(self, digest: StoredDigest, tenant_id: UUID | None = None) -> None:
        """Upsert a digest unless a newer ``source_version`` already exists."""
        if tenant_id is None:
            raise TenantRequired("brief SQL repository needs a tenant id")
        async with self._engine.begin() as connection:
            await set_tenant_context(connection, tenant_id)
            await connection.execute(
                _UPSERT,
                {
                    "tenant_id": tenant_id,
                    "company_id": digest.company_id,
                    "source_version": digest.source_version,
                    "content": digest.content.model_dump_json(),
                    "generated_at": datetime.fromtimestamp(digest.generated_at / 1000, tz=UTC),
                    "summarizer_version": digest.summarizer_version,
                    "degraded": digest.degraded,
                    "config_version": digest.config_version,
                },
            )


def create_repository(database_url: str | None) -> BriefStore:
    """Return the SQL repository when a database URL is set, else the in-memory one."""
    engine = create_engine(database_url)
    if engine is None:
        return BriefRepository(database_url)
    return SqlBriefRepository(engine)
