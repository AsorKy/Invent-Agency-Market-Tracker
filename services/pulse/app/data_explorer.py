"""Datasets of ``pulse`` for the read-only data explorer (``libs/common/data_explorer.py``).

``pulse.fact_signal`` and ``pulse.daily_rollup`` carry ``tenant_id`` under forced row level
security; the shared router sets ``app.tenant_id`` and the statements also filter
``tenant_id = :tenant_id``. Every statement is fixed and binds ``:tenant_id``, ``:q``,
``:k1`` ... and ``:limit``. Mounted by ``main.py``.
"""

from sqlalchemy import text

from libs.common.data_explorer import Dataset, data_router

DATASETS: tuple[Dataset, ...] = (
    Dataset(
        id="fact_signals",
        columns=(
            ("signal_id", "uuid"),
            ("tenant_id", "uuid"),
            ("company_id", "uuid"),
            ("source", "text"),
            ("signal_type", "text"),
            ("observed_at", "timestamptz"),
            ("strength", "numeric"),
            ("loaded_at", "timestamptz"),
        ),
        key=("observed_at", "signal_id"),
        page=text(
            """
            SELECT signal_id, tenant_id, company_id, source, signal_type, observed_at,
                   strength, loaded_at
            FROM pulse.fact_signal
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(company_id::text, lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(signal_type), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS timestamptz) IS NULL
                   OR (observed_at, signal_id) < (CAST(:k1 AS timestamptz), CAST(:k2 AS uuid)))
            ORDER BY observed_at DESC, signal_id DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM pulse.fact_signal
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(company_id::text, lower(CAST(:q AS text))) > 0
                   OR strpos(lower(source), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(signal_type), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
    Dataset(
        id="daily_rollups",
        columns=(
            ("tenant_id", "uuid"),
            ("rollup_date", "date"),
            ("city", "text"),
            ("service_line", "text"),
            ("signal_count", "integer"),
            ("lead_count", "integer"),
            ("hot_lead_count", "integer"),
            ("as_of", "timestamptz"),
            ("stale", "boolean"),
            ("low_sample", "boolean"),
        ),
        key=("rollup_date", "city", "service_line"),
        page=text(
            """
            SELECT tenant_id, rollup_date, city, service_line, signal_count, lead_count,
                   hot_lead_count, as_of, stale, low_sample
            FROM pulse.daily_rollup
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(lower(city), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(service_line), lower(CAST(:q AS text))) > 0)
              AND (CAST(:k1 AS date) IS NULL
                   OR (rollup_date, city, service_line)
                      < (CAST(:k1 AS date), CAST(:k2 AS text), CAST(:k3 AS text)))
            ORDER BY rollup_date DESC, city DESC, service_line DESC
            LIMIT :limit
            """
        ),
        count=text(
            """
            SELECT count(*) FROM pulse.daily_rollup
            WHERE tenant_id = :tenant_id
              AND (CAST(:q AS text) IS NULL
                   OR strpos(lower(city), lower(CAST(:q AS text))) > 0
                   OR strpos(lower(service_line), lower(CAST(:q AS text))) > 0)
            """
        ),
    ),
)

router = data_router("pulse", DATASETS)
