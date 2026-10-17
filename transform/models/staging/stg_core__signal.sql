-- Normalize core.signal for the analytics model.
-- It reads from the initial migration and relates to fact_signal.sql,
-- schema.yml, and the analytics consumer. Its purpose is to isolate the
-- transactional contract from the shape consumed by marts.

select
    id as signal_id,
    company_id,
    source,
    signal_type,
    observed_at,
    strength,
    payload
from {{ source('core', 'signal') }}

