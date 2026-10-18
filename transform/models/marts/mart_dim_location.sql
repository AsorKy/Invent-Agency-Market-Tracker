-- Location dimension. Natural key: location_id.

select
    location_id,
    company_id,
    city,
    department,
    address,
    latitude,
    longitude,
    h3_index
from {{ ref('stg_core__location') }}
