"""Close finished local days of ``pulse.daily_rollup`` (daily close job).

Run as ``python -m services.pulse.app.rollup_close --tenant <uuid> [--day
YYYY-MM-DD]``. The event consumer already keeps counters current, so this job
only closes the day (REQ-PUL-002): it sets ``low_sample`` when the day has
fewer than 5 signals, clears ``stale``, and refreshes ``as_of``. It never
recounts. The default day is yesterday in the default city timezone. It relates
to ``rollups.py`` and ``consumer.py`` and is safe to run repeatedly.
"""

import argparse
import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from libs.common.db import create_engine
from libs.common.settings import get_settings
from libs.common.tenant import set_tenant_context
from libs.common.time import local_day

LOW_SAMPLE_THRESHOLD = 5

CLOSE_SQL = """
UPDATE pulse.daily_rollup
SET low_sample = (signal_count < :threshold), stale = false, as_of = now()
WHERE tenant_id = :tenant_id AND rollup_date = :rollup_date
"""


def default_close_day(now: datetime | None = None) -> date:
    """Return the last fully elapsed local day."""
    return local_day(now or datetime.now(UTC)) - timedelta(days=1)


async def close_day(connection: AsyncConnection, tenant_id: UUID, rollup_date: date) -> None:
    """Close one tenant day; rows of other days are untouched."""
    await set_tenant_context(connection, tenant_id)
    await connection.execute(
        text(CLOSE_SQL),
        {
            "tenant_id": tenant_id,
            "rollup_date": rollup_date,
            "threshold": LOW_SAMPLE_THRESHOLD,
        },
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant", action="append", required=True, type=UUID)
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    engine = create_engine(get_settings().database_url, pool_size=2)
    if engine is None:
        raise RuntimeError("DATABASE_URL is required for the pulse close job")
    rollup_date = args.day or default_close_day()
    try:
        for tenant_id in args.tenant:
            async with engine.begin() as connection:
                await close_day(connection, tenant_id, rollup_date)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
