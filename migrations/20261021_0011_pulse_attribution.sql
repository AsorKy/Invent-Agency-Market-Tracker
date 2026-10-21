-- Expand: pulse.attribution stores attribution credit per channel (T-136, REQ-LNS-005).
--
-- One row per tenant, project, model, channel and source version; a rerun of the
-- same version upserts. credit is an exact decimal. project_id has no
-- foreign key: schemas stay decoupled. Forced row level security with the
-- standard app.tenant_id policy. pulse_app gets its grants from the default
-- privileges set by ops/bootstrap_roles.py. Additive only.

CREATE TABLE IF NOT EXISTS pulse.attribution (
    tenant_id uuid NOT NULL,
    project_id uuid NOT NULL,
    model text NOT NULL CHECK (model IN ('last_touch', 'first_touch', 'time_decay', 'markov', 'shapley')),
    channel text NOT NULL,
    credit numeric(18,9) NOT NULL,
    source_version text NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, model, channel, source_version)
);

CREATE INDEX IF NOT EXISTS ix_pulse_attribution_project
    ON pulse.attribution (tenant_id, project_id, computed_at);

ALTER TABLE pulse.attribution ENABLE ROW LEVEL SECURITY;
ALTER TABLE pulse.attribution FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'pulse' AND tablename = 'attribution'
          AND policyname = 'attribution_tenant_isolation'
    ) THEN
        CREATE POLICY attribution_tenant_isolation ON pulse.attribution
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
END $$;
