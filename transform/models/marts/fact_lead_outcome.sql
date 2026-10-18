-- Lead outcome fact. Grain: one row per recorded outcome.

select
    o.outcome_id,
    l.tenant_id,
    o.lead_id,
    l.company_id,
    l.service_line,
    o.outcome,
    o.occurred_at,
    o.occurred_at::date as occurred_date
from {{ ref('stg_core__lead_outcome') }} as o
inner join {{ ref('stg_core__lead') }} as l
    on l.lead_id = o.lead_id
