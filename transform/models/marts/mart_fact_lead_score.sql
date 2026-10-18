-- Daily lead score snapshot. Grain: company per score_date, keeping the last
-- score written that day.

select distinct on (h.company_id, h.as_of::date)
    c.tenant_id,
    h.company_id,
    h.as_of::date as score_date,
    h.score,
    h.model_version,
    h.source_version,
    h.as_of
from {{ ref('stg_ml__score_history') }} as h
left join {{ ref('stg_core__company') }} as c
    on c.company_id = h.company_id
order by h.company_id, h.as_of::date, h.as_of desc, h.score_history_id desc
