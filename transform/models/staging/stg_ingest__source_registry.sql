-- Registered ingestion sources; feeds dim_source.

select
    source,
    enabled,
    cadence,
    updated_at
from {{ source('ingest', 'source_registry') }}
