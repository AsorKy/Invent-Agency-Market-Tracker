"""Read the company facts that ``brief`` needs to build a digest (REQ-BRF-001).

``read_brief_facts`` runs tenant-scoped reads on an async SQLAlchemy engine and
returns the plain dict that ``services/brief/app/core_source.py`` merges into
``CompanyFacts``. A company of another tenant yields ``None`` so the route can
answer 404. Relates to ``main.py`` (route) and ``repository.py`` (engine owner).
RUES, quarantine, certificate, and enrichment facts are read from the company
``source_flags`` document; no task table exists, so ``overdue_tasks`` is 0.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

RUES_STATUSES = ("matched", "pending", "unmatched", "quarantined")


def _non_negative(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, int(value))


def _days_since(moment: datetime | None, now: datetime) -> int | None:
    return None if moment is None else max(0, (now - moment).days)


def build_facts(
    company: dict[str, Any],
    signal_codes: list[str],
    signal_versions_sum: int,
    activities: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Map rows to the brief facts payload (everything except ``company_id``)."""
    flags = company.get("source_flags")
    flags = flags if isinstance(flags, dict) else {}
    status = flags.get("rues_status")
    if status not in RUES_STATUSES:
        status = "matched" if company.get("nit") else "pending"
    quarantined_at = flags.get("quarantined_at")
    quarantine_days = 0
    if status == "quarantined" and isinstance(quarantined_at, str):
        try:
            since = datetime.fromisoformat(quarantined_at)
            since = since if since.tzinfo else since.replace(tzinfo=UTC)
            quarantine_days = _days_since(since, now) or 0
        except ValueError:
            quarantine_days = 0
    enrichment_flags = flags.get("enrichment_flags")
    last = activities[0]["occurred_at"] if activities else None
    return {
        "version": int(company["version"]),
        "signal_versions_sum": signal_versions_sum,
        "score": company.get("score"),
        "sector": company.get("sector_code"),
        "city": company.get("city"),
        "signal_codes": signal_codes,
        "rues_status": status,
        "rues_attempts": _non_negative(flags.get("rues_attempts")),
        "quarantine_days": quarantine_days,
        "domain_certificate_ok": flags.get("domain_certificate_ok") is not False,
        "enrichment_failed": flags.get("enrichment_failed") is True,
        "enrichment_flags": [str(f) for f in enrichment_flags]
        if isinstance(enrichment_flags, list)
        else [],
        "days_since_contact": _days_since(last, now),
        "overdue_tasks": 0,
        "activity": [
            {"type": str(a["activity_type"]), "occurred_at": a["occurred_at"].isoformat()}
            for a in activities
        ],
    }


async def read_brief_facts(
    engine: AsyncEngine, company_id: UUID, tenant_id: UUID | None
) -> dict[str, Any] | None:
    """Return the facts for one company, or ``None`` if unknown for the tenant."""
    params = {"company_id": company_id, "tenant_id": tenant_id}
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT c.version, c.nit, c.sector_code, c.city, c.source_flags, s.score
                        FROM core.company c
                        LEFT JOIN ml.lead_score s ON s.company_id = c.id
                        WHERE c.id = :company_id AND c.valid_to IS NULL
                          AND (CAST(:tenant_id AS uuid) IS NULL OR c.tenant_id = :tenant_id)
                        """
                    ),
                    params,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        signals = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT sg.signal_type, sg.version
                        FROM core.signal sg
                        WHERE sg.company_id = :company_id
                          AND (CAST(:tenant_id AS uuid) IS NULL OR sg.tenant_id = :tenant_id)
                        """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        activities = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT a.activity_type, a.occurred_at
                        FROM core.lead_activity a
                        JOIN core.lead l ON l.id = a.lead_id
                        WHERE l.company_id = :company_id
                          AND (CAST(:tenant_id AS uuid) IS NULL OR l.tenant_id = :tenant_id)
                        ORDER BY a.occurred_at DESC, a.id DESC
                        LIMIT 10
                        """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
    return build_facts(
        dict(row),
        sorted({str(s["signal_type"]) for s in signals}),
        sum(int(s["version"]) for s in signals),
        [dict(a) for a in activities],
        datetime.now(UTC),
    )
