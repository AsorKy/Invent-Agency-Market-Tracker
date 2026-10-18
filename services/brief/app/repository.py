"""Provide the Brief digest persistence seam.

The in-memory repository makes the new service importable before the
``brief.record_digest`` and ``brief.single_flight_lock`` migrations exist. It
relates to ``main.py`` and preserves the same latest-by-company behavior that
the future Postgres repository must implement.
"""

import asyncio
from uuid import UUID

from services.brief.app.models import StoredDigest


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

    async def get_latest(self, company_id: UUID) -> StoredDigest | None:
        """Return the most recent digest for a company, if one exists."""
        async with self._lock:
            return self._records.get(company_id)

    async def save(self, digest: StoredDigest) -> None:
        """Persist a digest when it is newer than the current record."""
        async with self._lock:
            current = self._records.get(digest.company_id)
            if current is None or digest.source_version >= current.source_version:
                self._records[digest.company_id] = digest
