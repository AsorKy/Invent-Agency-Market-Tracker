"""Project ``signal.converted_to_project`` into the Pulse lens project tables.

The only catalog event that names a project is ``signal.converted_to_project``
(payload ``project_id``, ``service_line``). It creates the project in the
``draft`` stage, so the snapshot row and its first open stage visit are written
by one statement: the visit is inserted only when the snapshot row is new, which
keeps a replay of the same project from adding a second visit (REQ-EVT-002).
``project.pipeline_updated`` and ``project.forecast_updated`` (D-22, a lead stage
change) move the snapshot stage and close the open stage visit (``exited_at``
lives in ``project_stage_visit``), then open a visit for the new stage. They carry
no figure, so no score, value or burn is written. Events with a null project id
or for a project without a snapshot row change nothing, and a stage equal to the
current one is a no-op. It relates to ``consumer.py`` and ``lens.py``.
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


STAGE_SQL = """
UPDATE pulse.project_snapshot
SET stage = :stage, updated_at = now()
WHERE tenant_id = :tenant_id AND project_id = :project_id
  AND stage IS DISTINCT FROM :stage
RETURNING project_id
"""

CLOSE_VISIT_SQL = """
UPDATE pulse.project_stage_visit
SET exited_at = now()
WHERE tenant_id = :tenant_id AND project_id = :project_id AND exited_at IS NULL
"""

OPEN_VISIT_SQL = """
INSERT INTO pulse.project_stage_visit (tenant_id, project_id, stage, entered_at)
VALUES (:tenant_id, :project_id, :stage, now())
ON CONFLICT DO NOTHING
"""


async def apply_stage_change(
    connection: AsyncConnection, tenant_id: UUID, payload: dict[str, object], stage_key: str
) -> None:
    """Move the project stage named by ``payload[stage_key]``; skip when no project is linked."""
    raw_project, stage = payload.get("project_id"), payload.get(stage_key)
    if not raw_project or not stage:
        return
    params = {"tenant_id": tenant_id, "project_id": UUID(str(raw_project)), "stage": str(stage)}
    moved = await connection.execute(text(STAGE_SQL), params)
    if moved.scalar_one_or_none() is None:
        return
    await connection.execute(text(CLOSE_VISIT_SQL), params)
    await connection.execute(text(OPEN_VISIT_SQL), params)
