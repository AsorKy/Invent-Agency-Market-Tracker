-- CONTRACT: retire the legacy socioeconomic schema (SPEC D-01, REQ-DAT-001).
--
-- socio is the canonical schema (migration 20261017_0002). Existing rows are
-- copied into socio before the legacy schema is dropped, so no evidence is
-- lost. Apply this file only after no service reads socioeconomic, and commit
-- it separately from application code.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'socioeconomic') THEN
        INSERT INTO socio.source_dataset
            (dataset_name, provider, geography_level, refreshed_at, metadata)
        SELECT dataset_name, provider, geography_level, refreshed_at, metadata
        FROM socioeconomic.source_dataset
        ON CONFLICT (dataset_name) DO NOTHING;

        INSERT INTO socio.indicator_raw
            (dataset_name, territory_code, indicator, value, period, payload)
        SELECT dataset_name, territory_code, indicator, value, period, payload
        FROM socioeconomic.indicator_raw
        ON CONFLICT (dataset_name, territory_code, indicator, period) DO NOTHING;

        INSERT INTO socio.indicator_normalized
            (territory_code, indicator, period, z_score, normalized_value, method)
        SELECT territory_code, indicator, period, z_score, normalized_value, method
        FROM socioeconomic.indicator_normalized
        ON CONFLICT (territory_code, indicator, period) DO NOTHING;

        INSERT INTO socio.composite_index
            (territory_code, index_name, value, method, as_of)
        SELECT territory_code, index_name, value, method, as_of
        FROM socioeconomic.composite_index
        ON CONFLICT (territory_code, index_name) DO NOTHING;
    END IF;
END $$;

DROP SCHEMA IF EXISTS socioeconomic CASCADE;
