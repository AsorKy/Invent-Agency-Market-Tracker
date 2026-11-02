"""Datasets of ``brief`` for the read-only data explorer (``libs/common/data_explorer.py``).

``brief.record_digest`` carries ``tenant_id`` under forced row level security; the shared
router sets ``app.tenant_id`` and the statements also filter ``tenant_id = :tenant_id``. Every
statement is fixed and binds ``:tenant_id``, ``:q``, ``:k1`` ... and ``:limit``. Mounted by
``main.py``.
"""

from sqlalchemy import text

from libs.common.data_explorer import Dataset, data_router

DATASETS: tuple[Dataset, ...] = (
    Dataset(
        id="record_digests",
        columns=(
            ("tenant_id", "uuid"),
            ("company_id", "uuid"),
            ("source_version", "bigint"),
            ("config_version", "bigint"),
            ("content", "jsonb"),
            ("generated_at", "timestamptz"),
            ("summarizer_version", "text"),
            ("degraded", "boolean"),
        ),
        key=("generated_at", "company_id", "source_version"),
        page=text(
            """
            SELECT tenant_id, company_id, source_version, config_version,
                   left(content::text, 20001) AS content, generated_at, summarizer_version,
                   degraded
            FROM brief.record_digest
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(company_id::text, lower(CAST(:q AS text))) > 0
                   OR strpos(lower(summarizer_version), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (generated_at, company_id, source_version)
                      < (CAST(:k1 AS timestamptz), CAST(:k2 AS uuid), CAST(:k3 AS bigint)))
            ORDER BY generated_at DESC, company_id DESC, source_version DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM brief.record_digest
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(company_id::text, lower(CAST(:q AS text))) > 0
                   OR strpos(lower(summarizer_version), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
)

router = data_router("brief", DATASETS)
