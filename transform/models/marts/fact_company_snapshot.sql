-- Daily company snapshot. Grain: company per snapshot_date. Each build adds the
-- current state as of today; history accumulates in the Parquet export.

select
    tenant_id,
    current_date as snapshot_date,
    company_id,
    employee_count,
    city,
    website is not null as has_website
from {{ ref('stg_core__company') }}
