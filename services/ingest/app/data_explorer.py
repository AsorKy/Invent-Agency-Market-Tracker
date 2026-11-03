"""Datasets of ``ingest`` for the read-only data explorer (``libs/common/data_explorer.py``).

The ingest tables hold the Bright Data material as delivered and the first steps derived from
it. They have no ``tenant_id`` column: ingest writes on behalf of exactly one tenant, the one in
``INGEST_TENANT_ID`` (``tenant.py``), and ``ops/bd_import.py`` lands through the same tenant. So
``ingest_tenant_scope`` shows every row to that tenant and no row to any other one; when the
variable is missing it refuses with 503 instead of guessing. The hash columns
(``payload_sha256``, ``candidate_key``) are never returned. Every statement is fixed and binds
``:q``, ``:k1`` ... and ``:limit``. Mounted by ``main.py``.
"""

from typing import Any
from uuid import UUID

from fastapi import Request
from pydantic import ValidationError
from sqlalchemy import text

from libs.common.data_explorer import Dataset, ScopeUnavailable, data_router
from services.ingest.app.tenant import load_ingest_tenant_id


async def ingest_tenant_scope(request: Request, tenant_id: UUID) -> dict[str, Any] | None:
    """Every ingest row belongs to ``INGEST_TENANT_ID``; other tenants see none."""
    try:
        owner = load_ingest_tenant_id()
    except ValidationError as exc:
        raise ScopeUnavailable("INGEST_TENANT_ID is not set") from exc
    return {} if tenant_id == owner else None


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        id="raw_snapshots",
        columns=(
            ("id", "uuid"),
            ("snapshot_id", "text"),
            ("dataset_id", "text"),
            ("source", "text"),
            ("payload", "jsonb"),
            ("fetched_at", "timestamptz"),
            ("created_at", "timestamptz"),
        ),
        key=("created_at", "id"),
        hidden=("payload_sha256",),
        page=text(
            """
            SELECT id, snapshot_id, dataset_id, source,
                   left(payload::text, 20001) AS payload, fetched_at, created_at
            FROM ingest.raw_snapshot
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(snapshot_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(dataset_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (created_at, id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS uuid)))
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM ingest.raw_snapshot
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(snapshot_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(dataset_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
    # One row per record of each snapshot payload (a JSON array; any other value counts as one
    # record), so a record fits in a cell where a whole snapshot (up to ~800 KB) does not.
    Dataset(
        id="raw_records",
        columns=(
            ("raw_snapshot_id", "uuid"),
            ("snapshot_id", "text"),
            ("dataset_id", "text"),
            ("source", "text"),
            ("record_index", "bigint"),
            ("record", "jsonb"),
            ("created_at", "timestamptz"),
        ),
        key=("created_at", "raw_snapshot_id", "record_index"),
        page=text(
            """
            SELECT id AS raw_snapshot_id, snapshot_id, dataset_id, source,
                   ord - 1 AS record_index, left(record::text, 20001) AS record, created_at
            FROM ingest.raw_snapshot
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(payload) = 'array'
                     THEN payload ELSE jsonb_build_array(payload) END
            ) WITH ORDINALITY AS r(record, ord)
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(snapshot_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(dataset_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(record::text), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (created_at, id, ord - 1) < (CAST(:k1 AS timestamptz),
                                                   CAST(:k2 AS uuid), CAST(:k3 AS bigint)))
            ORDER BY created_at DESC, raw_snapshot_id DESC, record_index DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*)
            FROM ingest.raw_snapshot
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(payload) = 'array'
                     THEN payload ELSE jsonb_build_array(payload) END
            ) AS r(record)
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(snapshot_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(dataset_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(record::text), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
    Dataset(
        id="entity_candidates",
        columns=(
            ("id", "uuid"),
            ("raw_snapshot_id", "uuid"),
            ("name", "text"),
            ("city", "text"),
            ("candidate_nit", "text"),
            ("normalized_name", "text"),
            ("resolution_attempts", "integer"),
            ("status", "text"),
            ("created_at", "timestamptz"),
        ),
        key=("created_at", "id"),
        hidden=("candidate_key",),
        page=text(
            """
            SELECT id, raw_snapshot_id, name, city, candidate_nit, normalized_name,
                   resolution_attempts, status, created_at
            FROM ingest.entity_candidate
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(name), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(city), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(status), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (created_at, id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS uuid)))
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM ingest.entity_candidate
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(name), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(city), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(status), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
    Dataset(
        id="rues_queue",
        columns=(
            ("id", "bigint"),
            ("candidate_id", "uuid"),
            ("status", "text"),
            ("attempts", "integer"),
            ("next_attempt_at", "timestamptz"),
            ("last_error", "text"),
            ("created_at", "timestamptz"),
        ),
        key=("created_at", "id"),
        page=text(
            """
            SELECT id, candidate_id, status, attempts, next_attempt_at, last_error, created_at
            FROM ingest.rues_lookup_queue
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(status), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(last_error), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (created_at, id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS bigint)))
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM ingest.rues_lookup_queue
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(status), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(last_error), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
    Dataset(
        id="failed_records",
        columns=(
            ("id", "bigint"),
            ("source", "text"),
            ("snapshot_id", "text"),
            ("record_ref", "text"),
            ("reason", "text"),
            ("payload", "jsonb"),
            ("created_at", "timestamptz"),
            ("resolved_at", "timestamptz"),
        ),
        key=("created_at", "id"),
        page=text(
            """
            SELECT id, source, snapshot_id, record_ref, reason,
                   left(payload::text, 20001) AS payload, created_at, resolved_at
            FROM ingest.failed_record
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(snapshot_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(record_ref), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(reason), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (created_at, id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS bigint)))
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM ingest.failed_record
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(snapshot_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(record_ref), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(reason), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
    Dataset(
        id="failed_fetches",
        columns=(
            ("id", "bigint"),
            ("source", "text"),
            ("external_id", "text"),
            ("error", "text"),
            ("attempts", "integer"),
            ("next_attempt_at", "timestamptz"),
            ("dead_lettered_at", "timestamptz"),
            ("created_at", "timestamptz"),
        ),
        key=("created_at", "id"),
        page=text(
            """
            SELECT id, source, external_id, error, attempts, next_attempt_at,
                   dead_lettered_at, created_at
            FROM ingest.failed_fetch
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(external_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(error), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (created_at, id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS bigint)))
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM ingest.failed_fetch
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(external_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(error), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
    Dataset(
        id="pending_snapshots",
        columns=(
            ("id", "uuid"),
            ("snapshot_id", "text"),
            ("source", "text"),
            ("payload", "jsonb"),
            ("status", "text"),
            ("attempts", "integer"),
            ("created_at", "timestamptz"),
        ),
        key=("created_at", "id"),
        hidden=("payload_sha256",),
        page=text(
            """
            SELECT id, snapshot_id, source, left(payload::text, 20001) AS payload, status,
                   attempts, created_at
            FROM ingest.pending_snapshot
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(snapshot_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(status), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (created_at, id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS uuid)))
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM ingest.pending_snapshot
            WHERE (CAST(:q AS text) IS NULL
                   OR strpos(lower(snapshot_id), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(status), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
)

router = data_router("ingest", DATASETS, ingest_tenant_scope)
