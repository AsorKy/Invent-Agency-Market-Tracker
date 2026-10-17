"""Read SQL projections for the analytics service.

The repository queries ``analytics.daily_rollup`` and
``analytics.territory_index`` through an asynchronous engine. It relates to
``services/analytics/app/main.py`` and the SQL migration, while the consumer
updates ``analytics.fact_signal``. Its purpose is to isolate SQL from HTTP
handlers and make unfinished F0 projections explicit.
"""

from sqlalchemy import text

from libs.common.db import create_engine


class AnalyticsRepository:
    def __init__(self, database_url: str | None) -> None:
        self.engine = create_engine(database_url, pool_size=4)

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()

    async def market_overview(self) -> dict[str, object]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for analytics reads")
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT COALESCE(SUM(signal_count), 0) AS signals,
                           COALESCE(SUM(lead_count), 0) AS leads,
                           COALESCE(SUM(hot_lead_count), 0) AS hot_leads,
                           max(as_of) AS as_of
                    FROM analytics.daily_rollup
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

    async def territory_opportunity(self) -> list[dict[str, object]]:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for analytics reads")
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT territory_code, ipet, imd, iop, as_of, source_version
                    FROM analytics.territory_index
                    ORDER BY iop DESC NULLS LAST
                    LIMIT 200
                    """
                )
            )
            return [dict(row) for row in result.mappings().all()]
