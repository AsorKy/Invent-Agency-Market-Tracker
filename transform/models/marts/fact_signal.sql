-- Signal mart for market queries and rollups.
-- Depends on stg_core__signal.sql and is documented in schema.yml; analytics
-- endpoints consume read models compatible with this projection.

select
    signal_id,
    company_id,
    source,
    signal_type,
    observed_at,
    strength
from {{ ref('stg_core__signal') }}

