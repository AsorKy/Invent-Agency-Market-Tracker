-- Company dimension (current version). Natural key: company_id.

select
    company_id,
    tenant_id,
    nit,
    slug,
    legal_name,
    trade_name,
    sector_code,
    employee_count,
    website is not null as has_website,
    city,
    created_at
from {{ ref('stg_core__company') }}
