-- Territory dimension from the territory codes that carry indicators.

select distinct
    territory_code
from {{ ref('stg_socio__indicator_normalized') }}
