-- Lead outcomes from the Core transactional plane; feeds mart_fact_lead_outcome.

select
    id as outcome_id,
    lead_id,
    outcome,
    occurred_at
from {{ source('core', 'lead_outcome') }}
