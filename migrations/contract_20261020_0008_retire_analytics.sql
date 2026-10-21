-- CONTRACT: retire the legacy analytics schema (SPEC D-01, REQ-DAT-001).
--
-- pulse is the canonical schema (migrations 20261017_0002 and 20261018_0004).
-- Legacy analytics rows that have a pulse equivalent are copied first. The
-- legacy tables carry no tenant_id, so the tenant is taken from the owning
-- core row; a row whose company or lead has no tenant cannot be attributed to
-- a tenant and is not copied. analytics.territory_index and
-- analytics.processed_event have no pulse equivalent and are dropped with the
-- schema. Apply only after no service reads analytics, and commit this file
-- separately from application code.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'analytics') THEN
        INSERT INTO pulse.fact_signal
            (signal_id, tenant_id, company_id, source, signal_type, observed_at,
             strength, loaded_at)
        SELECT f.signal_id, c.tenant_id, f.company_id, f.source, f.signal_type,
               f.observed_at, f.strength, f.loaded_at
        FROM analytics.fact_signal f
        JOIN core.company c ON c.id = f.company_id
        WHERE c.tenant_id IS NOT NULL
        ON CONFLICT (signal_id) DO NOTHING;

        INSERT INTO pulse.fact_company_snapshot
            (tenant_id, snapshot_date, company_id, employee_count, city, has_website)
        SELECT c.tenant_id, f.snapshot_date, f.company_id, f.employee_count,
               f.city, f.has_website
        FROM analytics.fact_company_snapshot f
        JOIN core.company c ON c.id = f.company_id
        WHERE c.tenant_id IS NOT NULL
        ON CONFLICT (tenant_id, snapshot_date, company_id) DO NOTHING;

        INSERT INTO pulse.fact_lead_outcome
            (outcome_id, tenant_id, lead_id, outcome, occurred_at)
        SELECT f.outcome_id, l.tenant_id, f.lead_id, f.outcome, f.occurred_at
        FROM analytics.fact_lead_outcome f
        JOIN core.lead l ON l.id = f.lead_id
        WHERE l.tenant_id IS NOT NULL
        ON CONFLICT (outcome_id) DO NOTHING;

        INSERT INTO pulse.semantic_metric_definition
            (metric_name, definition, grain, yaml_version, updated_at)
        SELECT metric_name, definition, grain, yaml_version, updated_at
        FROM analytics.semantic_metric_definition
        ON CONFLICT (metric_name) DO NOTHING;
    END IF;
END $$;

DROP SCHEMA IF EXISTS analytics CASCADE;
