-- Company locations from the Core transactional plane; feeds dim_location.

select
    id as location_id,
    company_id,
    city,
    department,
    address,
    latitude,
    longitude,
    h3_index
from {{ source('core', 'location') }}
