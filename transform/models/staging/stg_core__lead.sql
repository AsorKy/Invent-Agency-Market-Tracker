-- Leads from the Core transactional plane; feeds mart_dim_service_line and
-- mart_fact_lead_outcome.

select
    id as lead_id,
    tenant_id,
    company_id,
    service_line,
    stage,
    created_at,
    updated_at
from {{ source('core', 'lead') }}
