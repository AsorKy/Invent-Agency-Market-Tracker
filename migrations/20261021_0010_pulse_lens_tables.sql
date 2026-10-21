-- Expand: pulse tables for the lens results and the project read model (T-135, REQ-LNS-003, REQ-LNS-004).
--
-- company_profile, company_community and topic_result store what the ml
-- service publishes through POST /internal/pulse/ingest/*. project_snapshot and
-- project_stage_visit are the tenant-scoped inputs of the project descriptive
-- metrics. Every table has a NOT NULL tenant_id and forced row level security
-- with the standard app.tenant_id policy. project_id has no foreign key:
-- schemas stay decoupled. Additive only.

CREATE TABLE IF NOT EXISTS pulse.company_profile (
    tenant_id uuid NOT NULL,
    run_id text NOT NULL,
    company_ref text NOT NULL,
    algorithm text NOT NULL,
    archetype integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id, company_ref)
);

CREATE TABLE IF NOT EXISTS pulse.company_community (
    tenant_id uuid NOT NULL,
    run_id text NOT NULL,
    node_ref text NOT NULL,
    algorithm text NOT NULL,
    community integer NOT NULL,
    centrality numeric(18,10),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id, node_ref)
);

CREATE TABLE IF NOT EXISTS pulse.topic_result (
    tenant_id uuid NOT NULL,
    run_id text NOT NULL,
    period text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('top_terms', 'lda')),
    topic_index integer NOT NULL CHECK (topic_index >= 0),
    terms jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id, period, kind, topic_index)
);

CREATE TABLE IF NOT EXISTS pulse.project_snapshot (
    tenant_id uuid NOT NULL,
    project_id uuid NOT NULL,
    stage text NOT NULL,
    service_line text NOT NULL,
    score numeric(18,6),
    pipeline_value numeric(18,6),
    planned_burn numeric(18,6),
    actual_burn numeric(18,6),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id)
);

CREATE TABLE IF NOT EXISTS pulse.project_stage_visit (
    tenant_id uuid NOT NULL,
    project_id uuid NOT NULL,
    stage text NOT NULL,
    entered_at timestamptz NOT NULL,
    exited_at timestamptz,
    PRIMARY KEY (tenant_id, project_id, stage, entered_at)
);

CREATE INDEX IF NOT EXISTS ix_pulse_profile_run
    ON pulse.company_profile (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS ix_pulse_community_run
    ON pulse.company_community (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS ix_pulse_project_stage
    ON pulse.project_snapshot (tenant_id, stage);

DO $$
DECLARE
    table_name text;
    policy_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'company_profile', 'company_community', 'topic_result',
        'project_snapshot', 'project_stage_visit'
    ] LOOP
        EXECUTE format('ALTER TABLE pulse.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE pulse.%I FORCE ROW LEVEL SECURITY', table_name);
        policy_name := table_name || '_tenant_isolation';
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'pulse' AND tablename = table_name AND policyname = policy_name
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON pulse.%I
                 USING (tenant_id::text = current_setting(''app.tenant_id'', true))
                 WITH CHECK (tenant_id::text = current_setting(''app.tenant_id'', true))',
                policy_name,
                table_name
            );
        END IF;
    END LOOP;
END $$;
