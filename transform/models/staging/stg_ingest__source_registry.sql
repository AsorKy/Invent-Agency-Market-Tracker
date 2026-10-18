-- Registered ingestion sources; feeds mart_dim_source.

select
    source,
    enabled,
    cadence,
    updated_at
from {{ source('ingest', 'source_registry') }}
