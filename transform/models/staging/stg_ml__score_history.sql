-- Score history written by the ML service; feeds mart_fact_lead_score.

select
    id as score_history_id,
    company_id,
    score,
    source_version,
    model_version,
    as_of
from {{ source('ml', 'score_history') }}
