-- Current version of each company from the Core transactional plane.
-- Feeds dim_company, dim_sector and fact_company_snapshot; schema.yml documents it.

select
    id as company_id,
    tenant_id,
    nit,
    slug,
    legal_name,
    trade_name,
    sector_code,
    employee_count,
    website,
    city,
    created_at,
    updated_at
from {{ source('core', 'company') }}
where valid_to is null
