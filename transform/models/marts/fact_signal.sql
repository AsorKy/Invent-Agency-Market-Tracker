-- Signal fact for market queries and rollups. Grain: one row per signal.
-- Depends on stg_core__signal and stg_core__company; documented in schema.yml.

select
    s.signal_id,
    c.tenant_id,
    s.company_id,
    s.source,
    s.signal_type,
    s.observed_at,
    s.observed_at::date as observed_date,
    s.strength
from {{ ref('stg_core__signal') }} as s
left join {{ ref('stg_core__company') }} as c
    on c.company_id = s.company_id
