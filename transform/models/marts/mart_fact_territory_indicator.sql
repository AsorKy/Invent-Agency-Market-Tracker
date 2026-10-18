-- Territory indicator fact. Grain: territory, indicator and period.

select
    territory_code,
    indicator,
    period,
    z_score,
    normalized_value,
    method,
    source_version
from {{ ref('stg_socio__indicator_normalized') }}
