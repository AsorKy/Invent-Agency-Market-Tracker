-- Leads from the Core transactional plane; feeds dim_service_line and
-- fact_lead_outcome.

select
    id as lead_id,
    tenant_id,
    company_id,
    service_line,
    stage,
    created_at,
    updated_at
from {{ source('core', 'lead') }}
