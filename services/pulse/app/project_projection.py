"""Project ``signal.converted_to_project`` into the Pulse lens project tables.

The only catalog event that names a project is ``signal.converted_to_project``
(payload ``project_id``, ``service_line``). It creates the project in the
``draft`` stage, so the snapshot row and its first open stage visit are written
by one statement: the visit is inserted only when the snapshot row is new, which
keeps a replay of the same project from adding a second visit (REQ-EVT-002).
No event carries a later stage change, score, value or burn, so those columns
stay empty. It relates to ``consumer.py`` and ``lens.py``.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

INITIAL_STAGE = "draft"

PROJECT_SQL = """
WITH inserted AS (
    INSERT INTO pulse.project_snapshot (tenant_id, project_id, stage, service_line)
    VALUES (:tenant_id, :project_id, :stage, :service_line)
    ON CONFLICT (tenant_id, project_id) DO NOTHING
    RETURNING tenant_id, project_id, stage
)
INSERT INTO pulse.project_stage_visit (tenant_id, project_id, stage, entered_at)
SELECT tenant_id, project_id, stage, now() FROM inserted
ON CONFLICT DO NOTHING
"""


async def apply_project_created(
    connection: AsyncConnection, tenant_id: UUID, payload: dict[str, object]
) -> None:
    """Store the project snapshot and its first stage visit for the event tenant."""
    await connection.execute(
        text(PROJECT_SQL),
        {
            "tenant_id": tenant_id,
            "project_id": UUID(str(payload["project_id"])),
            "stage": INITIAL_STAGE,
            "service_line": str(payload.get("service_line") or "unknown"),
        },
    )
