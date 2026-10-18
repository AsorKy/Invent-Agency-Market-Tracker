-- Source dimension from the ingestion source registry.

select
    source,
    enabled,
    cadence
from {{ ref('stg_ingest__source_registry') }}
