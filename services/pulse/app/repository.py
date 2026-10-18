"""Provide the Pulse read-model seam.

The repository owns only Pulse-specific read methods and intentionally returns
empty projections until the v2.2 Pulse schema and event consumer are added.
It relates to ``main.py`` and the future ``pulse`` migrations; keeping this
boundary in the service lets the HTTP contract be validated without coupling
the shell to the legacy ``analytics`` tables.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from libs.common.db import create_engine
from services.pulse.app.models import (
    PipelineBacklog,
    PipelineTimeGroup,
    SignalPoint,
    SourceEfficiency,
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
