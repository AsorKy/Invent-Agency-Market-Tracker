-- Normalized socioeconomic indicators; feeds dim_territory and
-- fact_territory_indicator.

select
    territory_code,
    indicator,
    period,
    z_score,
    normalized_value,
    method,
    source_version
from {{ source('socio', 'indicator_normalized') }}
