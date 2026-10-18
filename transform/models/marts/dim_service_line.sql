-- Service line dimension derived from the service lines present on leads.

select distinct
    service_line
from {{ ref('stg_core__lead') }}
