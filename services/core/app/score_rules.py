"""Serve the active lead score rules of one tenant to ``ml`` (REQ-ML-001, REQ-SEC-005).

``ml_app`` has no grant on the ``core`` schema (INV-01), so ``ml`` asks core over the
signed internal API. ``read_score_rules`` runs inside one transaction that calls
``set_tenant_context``, filters by tenant again, and answers an empty list for an unknown
tenant. ``version`` is a stable checksum of the returned rules, 0 when there are none.
The body is parsed by ``services/ml/app/core_rules.py``. Relates to ``main.py`` (route).
"""

import zlib
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from libs.common.tenant import set_tenant_context


def build_rules_body(rows: list[tuple[str, Decimal | float, bool]]) -> dict[str, object]:
    """Map ``(rule_name, weight, active)`` rows to the response body."""
    rules = sorted(((str(n), float(w), bool(a)) for n, w, a in rows), key=lambda r: r[0])
    version = zlib.crc32(repr(rules).encode("ascii")) if rules else 0
    return {
        "rules": [{"rule_name": n, "weight": w, "active": a} for n, w, a in rules],
        "version": version,
    }


async def read_score_rules(engine: AsyncEngine, tenant_id: UUID) -> dict[str, object]:
    """Return the rules of ``tenant_id`` only."""
    async with engine.begin() as connection:
        await set_tenant_context(connection, tenant_id)
        result = await connection.execute(
            text(
                "SELECT rule_name, weight, active FROM core.lead_score_rule "
                "WHERE tenant_id = :tenant_id ORDER BY rule_name"
            ),
            {"tenant_id": tenant_id},
        )
        rows = [(str(r[0]), r[1], bool(r[2])) for r in result.all()]
    return build_rules_body(rows)
