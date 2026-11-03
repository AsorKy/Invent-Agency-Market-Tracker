"""Datasets of ``core`` for the read-only data explorer (``libs/common/data_explorer.py``).

``core.company``, ``core.signal`` and ``core.lead`` carry ``tenant_id``. The shared router sets
``app.tenant_id`` for row level security and every statement also filters
``tenant_id = :tenant_id``, so legacy rows without a tenant (visible to every tenant under the
F0 company and lead policies) never show up. Every statement is fixed and binds ``:tenant_id``,
``:q``, ``:k1`` ... and ``:limit``. Mounted by ``main.py``.
"""

from sqlalchemy import text

from libs.common.data_explorer import Dataset, data_router

DATASETS: tuple[Dataset, ...] = (
    Dataset(
        id="companies",
        columns=(
            ("id", "uuid"),
            ("tenant_id", "uuid"),
            ("nit", "text"),
            ("slug", "text"),
            ("legal_name", "text"),
            ("trade_name", "text"),
            ("sector_code", "text"),
            ("employee_count", "integer"),
            ("website", "text"),
            ("city", "text"),
            ("source_flags", "jsonb"),
            ("score", "integer"),
            ("score_source_version", "text"),
            ("score_updated_at", "timestamptz"),
            ("score_event_id", "uuid"),
            ("version", "bigint"),
            ("valid_from", "timestamptz"),
            ("valid_to", "timestamptz"),
            ("created_at", "timestamptz"),
            ("updated_at", "timestamptz"),
        ),
        key=("updated_at", "id"),
        page=text(
            """
            SELECT id, tenant_id, nit, slug, legal_name, trade_name, sector_code,
                   employee_count, website, city, left(source_flags::text, 20001) AS source_flags,
                   score, score_source_version, score_updated_at, score_event_id, version,
                   valid_from, valid_to, created_at, updated_at
            FROM core.company
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(lower(legal_name), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(trade_name), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(slug), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(city), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (updated_at, id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS uuid)))
            ORDER BY updated_at DESC, id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM core.company
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(lower(legal_name), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(trade_name), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(slug), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(city), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
    Dataset(
        id="signals",
        columns=(
            ("id", "uuid"),
            ("tenant_id", "uuid"),
            ("company_id", "uuid"),
            ("source", "text"),
            ("source_external_id", "text"),
            ("signal_type", "text"),
            ("observed_at", "timestamptz"),
            ("strength", "numeric"),
            ("payload", "jsonb"),
            ("version", "bigint"),
            ("created_at", "timestamptz"),
        ),
        key=("observed_at", "id"),
        page=text(
            """
            SELECT id, tenant_id, company_id, source, source_external_id, signal_type,
                   observed_at, strength, left(payload::text, 20001) AS payload, version,
                   created_at
            FROM core.signal
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(signal_type), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source_external_id), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (observed_at, id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS uuid)))
            ORDER BY observed_at DESC, id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM core.signal
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(signal_type), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source_external_id), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
    Dataset(
        id="leads",
        columns=(
            ("id", "uuid"),
            ("tenant_id", "uuid"),
            ("company_id", "uuid"),
            ("project_id", "uuid"),
            ("service_line", "text"),
            ("stage", "text"),
            ("source", "text"),
            ("city", "text"),
            ("holdout", "boolean"),
            ("qualification_reason", "jsonb"),
            ("version", "bigint"),
            ("updated_by", "uuid"),
            ("last_transition_at", "timestamptz"),
            ("created_at", "timestamptz"),
            ("updated_at", "timestamptz"),
        ),
        key=("updated_at", "id"),
        page=text(
            """
            SELECT id, tenant_id, company_id, project_id, service_line, stage, source, city,
                   holdout, left(qualification_reason::text, 20001) AS qualification_reason,
                   version, updated_by, last_transition_at, created_at, updated_at
            FROM core.lead
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(lower(service_line), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(stage), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(city), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (updated_at, id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS uuid)))
            ORDER BY updated_at DESC, id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM core.lead
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(lower(service_line), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(stage), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(city), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
)

router = data_router("core", DATASETS)
