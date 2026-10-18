"""Set the transaction-local tenant context used by PostgreSQL RLS.

The helper relates to identity claims, gateway propagation, and the tenant
policies in the ARCH-0001 v2.2 migrations. It provides one safe database hook
for services to call after a signed tenant claim has been validated and before
tenant-owned queries or writes are executed.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def set_tenant_context(connection: AsyncConnection, tenant_id: UUID) -> None:
    """Set ``app.tenant_id`` for the current transaction only."""

    await connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
