"""Provide the Pulse read-model seam.

The repository owns the Pulse read methods. The market overview reads
``pulse.daily_rollup``; the five metric methods still return empty projections
until the rollup tasks fill them. It relates to ``main.py``, ``consumer.py``,
and the ``pulse`` migrations, and keeps SQL out of the HTTP handlers.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from libs.common.db import create_engine
from libs.common.tenant import set_tenant_context
from services.pulse.app.models import (
    PipelineBacklog,
    PipelineTimeGroup,
    SignalPoint,
    SourceEfficiency,
    SourceFreshness,
    TopSource,
)


class PulseRepository:
    """Expose empty-safe reads for the future Pulse projections."""

    def __init__(self, database_url: str | None) -> None:
        self.engine: AsyncEngine | None = create_engine(database_url, pool_size=2)

    async def close(self) -> None:
        """Dispose the optional database engine during application shutdown."""
        if self.engine is not None:
            await self.engine.dispose()

    async def market_overview(self, *, tenant_id: UUID | None) -> dict[str, object]:
        """Return 90-day signal and lead totals from the daily rollup."""
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for pulse reads")
        async with self.engine.begin() as connection:
            if tenant_id is not None:
                await set_tenant_context(connection, tenant_id)
            result = await connection.execute(
                text(
                    """
                    SELECT COALESCE(SUM(signal_count), 0) AS signals,
                           COALESCE(SUM(lead_count), 0) AS leads,
                           COALESCE(SUM(hot_lead_count), 0) AS hot_leads,
                           max(as_of) AS as_of
                    FROM pulse.daily_rollup
                    WHERE rollup_date >= current_date - 90
                    """
                )
            )
            row = result.mappings().one()
            return {
                "signals": row["signals"],
                "leads": row["leads"],
                "hot_leads": row["hot_leads"],
                "as_of": row["as_of"],
            }

    async def unprocessed_signals(
        self,
        *,
        granularity: str,
        from_date: str | None,
        to_date: str | None,
        city: str | None,
    ) -> tuple[list[SignalPoint], datetime | None]:
        """Return signal buckets and their source timestamp.

        Query arguments are accepted now so the future event-backed read model
        can be introduced without changing the endpoint contract.
        """
        del granularity, from_date, to_date, city
        return [], None

    async def pipeline_backlog(
        self, *, city: str | None
    ) -> tuple[PipelineBacklog | None, datetime | None]:
        """Return backlog metrics or an empty projection."""
        del city
        return None, None

    async def source_efficiency(
        self, *, period: str, service_line: str | None
    ) -> tuple[list[SourceEfficiency], datetime | None]:
        """Return source efficiency rows or an empty projection."""
        del period, service_line
        return [], None

    async def time_in_pipeline(
        self, *, group_by: str
    ) -> tuple[list[PipelineTimeGroup], datetime | None]:
        """Return pipeline timing groups or an empty projection."""
        del group_by
        return [], None

    async def top_sources(
        self, *, limit: int, service_line: str
    ) -> tuple[list[TopSource], datetime | None]:
        """Return a bounded source ranking or an empty projection."""
        del limit, service_line
        return [], None

    async def ops_health(
        self,
    ) -> tuple[list[SourceFreshness], int | None, datetime | None]:
        """Return source freshness, outbox backlog, and their timestamp, or empty."""
        return [], None, None
