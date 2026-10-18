-- Normalized socioeconomic indicators; feeds mart_dim_territory and
-- mart_fact_territory_indicator.

select
    territory_code,
    indicator,
    period,
    z_score,
    normalized_value,
    method,
    source_version
from {{ source('socio', 'indicator_normalized') }}
