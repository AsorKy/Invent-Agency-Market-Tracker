-- EXPAND: run 1 schema additions to reach the SPEC section 3.2 catalog.
--
-- Adds the tables and columns that migrations 0001 and 0002 did not create:
-- ingest pending_snapshot, failed_record, rues_lookup_queue, processed_event;
-- core job, person_role, lead_score_rule; pulse analytics facts and the
-- semantic metric definitions (pulse replaces the legacy analytics schema);
-- lead source, city, holdout and lead_activity channel, source. Tenant-owned
-- new tables have fail-closed RLS. The RLS on company, lead, and project is
-- already enabled by 0001; it is re-asserted here (idempotent). Expand only:
-- every statement is additive and uses IF NOT EXISTS.
-- Relates to SPEC 3.2, REQ-COR-001, REQ-DAT-005, REQ-EXT-004, ADR E2.1.

-- ---------------------------------------------------------------------------
-- ingest
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingest.pending_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id text NOT NULL,
    source text NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, snapshot_id, payload_sha256)
);

CREATE TABLE IF NOT EXISTS ingest.failed_record (
    id bigserial PRIMARY KEY,
    source text NOT NULL,
    snapshot_id text,
    record_ref text,
    reason text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE TABLE IF NOT EXISTS ingest.rues_lookup_queue (
    id bigserial PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES ingest.entity_candidate(id),
    status text NOT NULL DEFAULT 'queued',
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (candidate_id)
);

CREATE TABLE IF NOT EXISTS ingest.processed_event (
    event_id uuid PRIMARY KEY,
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_pending_snapshot_status
    ON ingest.pending_snapshot (status, created_at);
CREATE INDEX IF NOT EXISTS ix_rues_queue_due
    ON ingest.rues_lookup_queue (status, next_attempt_at);

-- ---------------------------------------------------------------------------
-- core
-- ---------------------------------------------------------------------------

ALTER TABLE core.lead ADD COLUMN IF NOT EXISTS service_line text;
ALTER TABLE core.lead ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE core.lead ADD COLUMN IF NOT EXISTS city text;
ALTER TABLE core.lead ADD COLUMN IF NOT EXISTS holdout boolean;

ALTER TABLE core.lead_activity ADD COLUMN IF NOT EXISTS channel text;
ALTER TABLE core.lead_activity ADD COLUMN IF NOT EXISTS source text;

CREATE TABLE IF NOT EXISTS core.job (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    job_type text NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    result jsonb,
    error_message text,
    idempotency_key text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_job_tenant_status
    ON core.job (tenant_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS core.person_role (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    company_id uuid NOT NULL REFERENCES core.company(id),
    person_name text NOT NULL,
    role_title text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_person_role_tenant_company
    ON core.person_role (tenant_id, company_id);

CREATE TABLE IF NOT EXISTS core.lead_score_rule (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    rule_name text NOT NULL,
    weight numeric(8,4) NOT NULL DEFAULT 1,
    definition jsonb NOT NULL DEFAULT '{}'::jsonb,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, rule_name)
);

-- ---------------------------------------------------------------------------
-- pulse (replaces the legacy analytics schema)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pulse.fact_company_snapshot (
    tenant_id uuid NOT NULL,
    snapshot_date date NOT NULL,
    company_id uuid NOT NULL,
    employee_count integer,
    city text,
    has_website boolean,
    PRIMARY KEY (tenant_id, snapshot_date, company_id)
);

CREATE TABLE IF NOT EXISTS pulse.fact_lead_outcome (
    outcome_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    outcome text NOT NULL,
    occurred_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS pulse.semantic_metric_definition (
    metric_name text PRIMARY KEY,
    definition text NOT NULL,
    grain text NOT NULL,
    yaml_version text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Row level security
-- ---------------------------------------------------------------------------

ALTER TABLE core.company ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.lead ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.project ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    item text[];
    policy_name text;
BEGIN
    FOREACH item SLICE 1 IN ARRAY ARRAY[
        ['core', 'job'], ['core', 'person_role'], ['core', 'lead_score_rule'],
        ['pulse', 'fact_company_snapshot'], ['pulse', 'fact_lead_outcome']
    ] LOOP
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', item[1], item[2]);
        EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', item[1], item[2]);
        policy_name := item[2] || '_tenant_isolation';
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = item[1] AND tablename = item[2] AND policyname = policy_name
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON %I.%I
                 USING (tenant_id::text = current_setting(''app.tenant_id'', true))
                 WITH CHECK (tenant_id::text = current_setting(''app.tenant_id'', true))',
                policy_name,
                item[1],
                item[2]
            );
        END IF;
    END LOOP;
END $$;
