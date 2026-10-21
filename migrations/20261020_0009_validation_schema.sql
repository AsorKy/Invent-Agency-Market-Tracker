-- Expand: the validation schema of layer L2 (REQ-LNS-010, ADR-IMT-0002 section 2.5).
--
-- Six tables hold the persisted results of the validation metrics V1 to V6.
-- The schema is operated by the core roles (core_owner, core_app), see
-- ops/schemas.py SHARED_ROLE_SCHEMAS. Every table carries source_version,
-- project_id, and measured_at, plus tenant_id for row level security.
-- project_id has no foreign key: schemas stay decoupled, as in pulse.
-- Money and ratios use numeric, never float.

CREATE SCHEMA IF NOT EXISTS validation;

-- V1: financial viability of a project.
CREATE TABLE IF NOT EXISTS validation.financial_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    project_id uuid NOT NULL,
    source_version text NOT NULL,
    measured_at timestamptz NOT NULL,
    discount_rate numeric(8,6) NOT NULL,
    npv numeric(18,2),
    irr numeric(10,6),
    payback_periods numeric(10,2),
    discounted_payback_periods numeric(10,2),
    emv numeric(18,2),
    assumptions jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- V2: unit economics.
CREATE TABLE IF NOT EXISTS validation.unit_economics (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    project_id uuid NOT NULL,
    source_version text NOT NULL,
    measured_at timestamptz NOT NULL,
    cac numeric(18,2),
    ltv numeric(18,2),
    ltv_cac_ratio numeric(12,4),
    cac_payback_months numeric(10,2),
    gross_margin numeric(8,6),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- V3: commercial funnel.
CREATE TABLE IF NOT EXISTS validation.funnel_metric (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    project_id uuid NOT NULL,
    source_version text NOT NULL,
    measured_at timestamptz NOT NULL,
    conversion_rate numeric(8,6),
    win_rate numeric(8,6),
    cycle_time_days numeric(10,2),
    velocity numeric(18,4),
    coverage numeric(10,4),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- V4: growth and efficiency.
CREATE TABLE IF NOT EXISTS validation.growth_metric (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    project_id uuid NOT NULL,
    source_version text NOT NULL,
    measured_at timestamptz NOT NULL,
    mrr numeric(18,2),
    nrr numeric(10,6),
    grr numeric(10,6),
    rule_of_40 numeric(10,4),
    burn_multiple numeric(12,4),
    magic_number numeric(12,4),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- V5: portfolio concentration and risk-adjusted return.
CREATE TABLE IF NOT EXISTS validation.portfolio_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    project_id uuid NOT NULL,
    source_version text NOT NULL,
    measured_at timestamptz NOT NULL,
    horizon_mix jsonb NOT NULL DEFAULT '{}'::jsonb,
    hhi numeric(8,6),
    sharpe_like numeric(12,6),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- V6: meta-ML metrics of the platform itself.
CREATE TABLE IF NOT EXISTS validation.meta_ml_metric (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    project_id uuid NOT NULL,
    source_version text NOT NULL,
    measured_at timestamptz NOT NULL,
    model_utility numeric(12,6),
    decision_latency_days numeric(10,2),
    psi numeric(10,6),
    precision_at_k numeric(8,6),
    funnel_lift numeric(12,6),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Access pattern: latest measurement of one project inside one tenant.
CREATE INDEX IF NOT EXISTS ix_financial_run_project
    ON validation.financial_run (tenant_id, project_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS ix_unit_economics_project
    ON validation.unit_economics (tenant_id, project_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS ix_funnel_metric_project
    ON validation.funnel_metric (tenant_id, project_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS ix_growth_metric_project
    ON validation.growth_metric (tenant_id, project_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS ix_portfolio_snapshot_project
    ON validation.portfolio_snapshot (tenant_id, project_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS ix_meta_ml_metric_project
    ON validation.meta_ml_metric (tenant_id, project_id, measured_at DESC);

-- Row level security, same policy as the other tenant tables.
DO $$
DECLARE
    table_name text;
    policy_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'financial_run', 'unit_economics', 'funnel_metric',
        'growth_metric', 'portfolio_snapshot', 'meta_ml_metric'
    ] LOOP
        EXECUTE format('ALTER TABLE validation.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE validation.%I FORCE ROW LEVEL SECURITY', table_name);
        policy_name := table_name || '_tenant_isolation';
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'validation'
              AND tablename = table_name
              AND policyname = policy_name
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON validation.%I
                 USING (tenant_id::text = current_setting(''app.tenant_id'', true))
                 WITH CHECK (tenant_id::text = current_setting(''app.tenant_id'', true))',
                policy_name,
                table_name
            );
        END IF;
    END LOOP;
END $$;

-- Grants for the core roles. They may not exist in local Compose (the migration
-- runs as postgres before the bootstrap), so the grant is conditional; the
-- bootstrap grants the same on every run.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'core_app') THEN
        GRANT USAGE ON SCHEMA validation TO core_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA validation TO core_app;
    END IF;
END $$;
