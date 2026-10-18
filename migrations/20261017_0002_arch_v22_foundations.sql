-- Add the ARCH-0001 v2.2 database foundation without rewriting the F0 schema.
--
-- This migration relates to identity, core, pulse, brief, and socio service
-- repositories, the outbox relay, consumer inboxes, and import workers. It is
-- an EXPAND migration: it adds schemas, tables, nullable compatibility columns,
-- indexes, and fail-closed policies for new tenant-scoped tables.
--
-- Existing F0 rows may have NULL tenant_id values. They are intentionally not
-- rejected or hidden by this migration. Before a future CONTRACT migration can
-- tighten existing core tables, operators must backfill tenant ownership,
-- propagate app.tenant_id from the authenticated request, and audit every
-- remaining NULL value. The old analytics and socioeconomic schemas remain
-- available while their service and data migrations are rolled out.

CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS pulse;
CREATE SCHEMA IF NOT EXISTS brief;
CREATE SCHEMA IF NOT EXISTS socio;

COMMENT ON SCHEMA identity IS
    'ARCH-0001 identity, tenant, membership, and scope data.';
COMMENT ON SCHEMA pulse IS
    'ARCH-0001 operational rollups and event projections.';
COMMENT ON SCHEMA brief IS
    'ARCH-0001 lead/company digests and single-flight coordination.';
COMMENT ON SCHEMA socio IS
    'ARCH-0001 Colombia socioeconomic reference data; canonical name for this schema.';

-- ---------------------------------------------------------------------------
-- Identity and tenancy foundations
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS identity.tenant (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE,
    display_name text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS identity.user_account (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL,
    display_name text,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_identity_user_email_lower
    ON identity.user_account (lower(email));

CREATE TABLE IF NOT EXISTS identity.tenant_membership (
    tenant_id uuid NOT NULL REFERENCES identity.tenant(id),
    user_id uuid NOT NULL REFERENCES identity.user_account(id),
    role text NOT NULL CHECK (role IN ('admin', 'strategist', 'intake', 'bd_rep', 'analyst')),
    scopes jsonb NOT NULL DEFAULT '{}'::jsonb,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS identity.membership_scope (
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL,
    city text NOT NULL DEFAULT '',
    service_line text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, city, service_line),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.tenant_membership(tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_identity_membership_user
    ON identity.tenant_membership (user_id, active, tenant_id);
CREATE INDEX IF NOT EXISTS ix_identity_scope_lookup
    ON identity.membership_scope (tenant_id, user_id, city, service_line);

-- Membership rows are tenant-scoped. Identity login and tenant discovery are
-- handled by the identity service and remain outside this policy set.
ALTER TABLE identity.tenant_membership ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.tenant_membership FORCE ROW LEVEL SECURITY;
ALTER TABLE identity.membership_scope ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.membership_scope FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'identity'
          AND tablename = 'tenant_membership'
          AND policyname = 'tenant_membership_tenant_isolation'
    ) THEN
        CREATE POLICY tenant_membership_tenant_isolation ON identity.tenant_membership
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'identity'
          AND tablename = 'membership_scope'
          AND policyname = 'membership_scope_tenant_isolation'
    ) THEN
        CREATE POLICY membership_scope_tenant_isolation ON identity.membership_scope
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Expand existing Core rows for tenant-aware rollout
-- ---------------------------------------------------------------------------

-- These columns remain nullable because the F0 local seed data predates tenant
-- ownership. A later backfill must assign a tenant before nullability is
-- tightened in a separately reviewed contract migration.
ALTER TABLE core.location
    ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE core.signal
    ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE core.lead_outcome
    ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE core.audit_log
    ADD COLUMN IF NOT EXISTS tenant_id uuid;

ALTER TABLE core.lead
    ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 1;
ALTER TABLE core.lead
    ADD COLUMN IF NOT EXISTS updated_by uuid;
ALTER TABLE core.lead
    ADD COLUMN IF NOT EXISTS last_transition_at timestamptz;
ALTER TABLE core.project
    ADD COLUMN IF NOT EXISTS lead_id uuid;

INSERT INTO core.pipeline_stage (stage, ordinal, terminal) VALUES
    ('discovered', 1, false),
    ('qualified', 2, false),
    ('hot', 3, false)
ON CONFLICT (stage) DO NOTHING;

COMMENT ON COLUMN core.lead.version IS
    'Monotonic aggregate version used for ETag and expected_version conflicts.';
COMMENT ON COLUMN core.lead.updated_by IS
    'Identity user UUID responsible for the latest lead mutation.';
COMMENT ON COLUMN core.lead.last_transition_at IS
    'Timestamp of the latest controlled pipeline-stage transition.';

CREATE INDEX IF NOT EXISTS ix_company_tenant_cursor
    ON core.company (tenant_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_location_tenant_company
    ON core.location (tenant_id, company_id, city);
CREATE INDEX IF NOT EXISTS ix_signal_tenant_observed
    ON core.signal (tenant_id, observed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_lead_tenant_pipeline_cursor
    ON core.lead (tenant_id, stage, service_line, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_lead_outcome_tenant_time
    ON core.lead_outcome (tenant_id, lead_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_project_tenant_lead
    ON core.project (tenant_id, lead_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_audit_tenant_created
    ON core.audit_log (tenant_id, created_at DESC, id DESC);

-- ---------------------------------------------------------------------------
-- Core tenant configuration, activity, import, and idempotency foundations
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.tenant_config (
    tenant_id uuid PRIMARY KEY,
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    scoring_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
    brief_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
    bright_data_budget_usd numeric(14,2)
        CHECK (bright_data_budget_usd IS NULL OR bright_data_budget_usd >= 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.tenant_config_history (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    scoring_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
    brief_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
    bright_data_budget_usd numeric(14,2)
        CHECK (bright_data_budget_usd IS NULL OR bright_data_budget_usd >= 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    changed_by uuid,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    UNIQUE (tenant_id, version),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS core.lead_activity (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    lead_id uuid NOT NULL REFERENCES core.lead(id),
    activity_type text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    created_by uuid,
    idempotency_key text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_lead_activity_tenant_lead_time
    ON core.lead_activity (tenant_id, lead_id, occurred_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS core.upload_blob (
    upload_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    original_filename text NOT NULL,
    content_type text NOT NULL,
    content bytea NOT NULL CHECK (octet_length(content) <= 10485760),
    size_bytes integer GENERATED ALWAYS AS (octet_length(content)) STORED,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.import_job (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    upload_id uuid NOT NULL REFERENCES core.upload_blob(upload_id),
    entity text NOT NULL CHECK (entity IN ('companies', 'leads', 'activities')),
    idempotency_key text NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    total_rows integer NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
    processed_rows integer NOT NULL DEFAULT 0 CHECK (processed_rows >= 0),
    upserted_rows integer NOT NULL DEFAULT 0 CHECK (upserted_rows >= 0),
    duplicate_rows integer NOT NULL DEFAULT 0 CHECK (duplicate_rows >= 0),
    error_rows integer NOT NULL DEFAULT 0 CHECK (error_rows >= 0),
    error_message text,
    preserve_blob boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS core.import_row (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL REFERENCES core.import_job(job_id) ON DELETE CASCADE,
    entity text NOT NULL CHECK (entity IN ('companies', 'leads', 'activities')),
    row_number integer NOT NULL CHECK (row_number > 0),
    row_sha256 text NOT NULL CHECK (row_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'upserted', 'duplicate', 'error')),
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, entity, row_sha256),
    UNIQUE (job_id, row_number)
);

CREATE TABLE IF NOT EXISTS core.idempotency_key (
    tenant_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'completed', 'failed')),
    response_code integer CHECK (response_code IS NULL OR response_code BETWEEN 100 AND 599),
    response_body jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_import_job_tenant_status
    ON core.import_job (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_import_row_job_status
    ON core.import_row (tenant_id, job_id, status, row_number);
CREATE INDEX IF NOT EXISTS ix_idempotency_expiry
    ON core.idempotency_key (expires_at);

-- ---------------------------------------------------------------------------
-- Outbox metadata for retries, leases, inboxes, and republish
-- ---------------------------------------------------------------------------

-- These additions allow a relay rollout to claim rows and retry safely. The
-- nullable fields preserve old events until a tenant and envelope backfill is
-- completed. New application writes should populate them immediately.
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS schema_version integer NOT NULL DEFAULT 1;
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS occurred_at timestamptz;
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS request_id text;
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS last_error text;
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS claim_token uuid;
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS claimed_at timestamptz;
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS dead_lettered_at timestamptz;
ALTER TABLE core.outbox_event
    ADD COLUMN IF NOT EXISTS replay boolean NOT NULL DEFAULT false;

ALTER TABLE core.outbox_dead_letter
    ADD COLUMN IF NOT EXISTS source_outbox_id bigint;
ALTER TABLE core.outbox_dead_letter
    ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE core.outbox_dead_letter
    ADD COLUMN IF NOT EXISTS replay boolean NOT NULL DEFAULT false;

ALTER TABLE core.processed_event
    ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE core.processed_event
    ADD COLUMN IF NOT EXISTS event_type text;
ALTER TABLE core.processed_event
    ADD COLUMN IF NOT EXISTS replay boolean NOT NULL DEFAULT false;
ALTER TABLE analytics.processed_event
    ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE analytics.processed_event
    ADD COLUMN IF NOT EXISTS event_type text;
ALTER TABLE analytics.processed_event
    ADD COLUMN IF NOT EXISTS replay boolean NOT NULL DEFAULT false;
ALTER TABLE ml.processed_event
    ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE ml.processed_event
    ADD COLUMN IF NOT EXISTS event_type text;
ALTER TABLE ml.processed_event
    ADD COLUMN IF NOT EXISTS replay boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS ix_outbox_claimable
    ON core.outbox_event (COALESCE(next_attempt_at, created_at), id)
    WHERE published_at IS NULL AND dead_lettered_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_outbox_tenant_created
    ON core.outbox_event (tenant_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_outbox_dead_letter_tenant
    ON core.outbox_dead_letter (tenant_id, created_at DESC, id DESC);

-- ---------------------------------------------------------------------------
-- Pulse projections and consumer inbox
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pulse.fact_signal (
    signal_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    company_id uuid NOT NULL,
    source text NOT NULL,
    signal_type text NOT NULL,
    observed_at timestamptz NOT NULL,
    strength numeric(5,4) NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pulse.daily_rollup (
    tenant_id uuid NOT NULL,
    rollup_date date NOT NULL,
    city text NOT NULL,
    service_line text NOT NULL,
    signal_count integer NOT NULL DEFAULT 0 CHECK (signal_count >= 0),
    lead_count integer NOT NULL DEFAULT 0 CHECK (lead_count >= 0),
    hot_lead_count integer NOT NULL DEFAULT 0 CHECK (hot_lead_count >= 0),
    as_of timestamptz NOT NULL DEFAULT now(),
    stale boolean NOT NULL DEFAULT false,
    low_sample boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, rollup_date, city, service_line)
);

CREATE TABLE IF NOT EXISTS pulse.source_efficiency_rollup (
    tenant_id uuid NOT NULL,
    rollup_date date NOT NULL,
    source text NOT NULL,
    service_line text NOT NULL,
    cost_usd numeric(14,2),
    qualified_leads integer NOT NULL DEFAULT 0 CHECK (qualified_leads >= 0),
    meetings integer NOT NULL DEFAULT 0 CHECK (meetings >= 0),
    converted_projects integer NOT NULL DEFAULT 0 CHECK (converted_projects >= 0),
    as_of timestamptz NOT NULL DEFAULT now(),
    stale boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, rollup_date, source, service_line)
);

CREATE TABLE IF NOT EXISTS pulse.pipeline_time_rollup (
    tenant_id uuid NOT NULL,
    rollup_date date NOT NULL,
    city text NOT NULL,
    service_line text NOT NULL,
    p50_days numeric(10,2),
    p90_days numeric(10,2),
    sample_count integer NOT NULL DEFAULT 0 CHECK (sample_count >= 0),
    as_of timestamptz NOT NULL DEFAULT now(),
    stale boolean NOT NULL DEFAULT false,
    low_sample boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, rollup_date, city, service_line)
);

CREATE TABLE IF NOT EXISTS pulse.top_source_rollup (
    tenant_id uuid NOT NULL,
    rollup_date date NOT NULL,
    source text NOT NULL,
    service_line text NOT NULL,
    qualified_leads integer NOT NULL DEFAULT 0 CHECK (qualified_leads >= 0),
    converted_projects integer NOT NULL DEFAULT 0 CHECK (converted_projects >= 0),
    conversion_rate numeric(7,4),
    as_of timestamptz NOT NULL DEFAULT now(),
    stale boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, rollup_date, source, service_line)
);

CREATE TABLE IF NOT EXISTS pulse.processed_event (
    event_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    event_type text NOT NULL,
    replay boolean NOT NULL DEFAULT false,
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_pulse_signal_tenant_time
    ON pulse.fact_signal (tenant_id, observed_at DESC, signal_id DESC);
CREATE INDEX IF NOT EXISTS ix_pulse_daily_tenant_day
    ON pulse.daily_rollup (tenant_id, rollup_date DESC, city, service_line);
CREATE INDEX IF NOT EXISTS ix_pulse_source_tenant_day
    ON pulse.source_efficiency_rollup (tenant_id, rollup_date DESC, source, service_line);
CREATE INDEX IF NOT EXISTS ix_pulse_pipeline_tenant_day
    ON pulse.pipeline_time_rollup (tenant_id, rollup_date DESC, city, service_line);
CREATE INDEX IF NOT EXISTS ix_pulse_top_source_tenant_day
    ON pulse.top_source_rollup (tenant_id, rollup_date DESC, source, service_line);

-- ---------------------------------------------------------------------------
-- Brief digest, single-flight lock, and consumer inbox
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS brief.record_digest (
    tenant_id uuid NOT NULL,
    company_id uuid NOT NULL,
    source_version bigint NOT NULL CHECK (source_version > 0),
    content jsonb NOT NULL,
    generated_at timestamptz NOT NULL,
    summarizer_version text NOT NULL,
    degraded boolean NOT NULL DEFAULT false,
    PRIMARY KEY (company_id, source_version)
);

CREATE TABLE IF NOT EXISTS brief.single_flight_lock (
    tenant_id uuid NOT NULL,
    company_id uuid NOT NULL,
    locked_at timestamptz NOT NULL DEFAULT now(),
    locked_by text NOT NULL,
    expires_at timestamptz,
    PRIMARY KEY (company_id)
);

CREATE TABLE IF NOT EXISTS brief.processed_event (
    event_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    event_type text NOT NULL,
    replay boolean NOT NULL DEFAULT false,
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_brief_latest
    ON brief.record_digest (tenant_id, company_id, source_version DESC);
CREATE INDEX IF NOT EXISTS ix_brief_lock_expiry
    ON brief.single_flight_lock (tenant_id, expires_at);

-- ---------------------------------------------------------------------------
-- Socioeconomic schema under the canonical ARCH-0001 name
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS socio.source_dataset (
    dataset_name text PRIMARY KEY,
    provider text NOT NULL,
    geography_level text NOT NULL,
    refreshed_at timestamptz,
    source_version bigint NOT NULL DEFAULT 1 CHECK (source_version > 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS socio.indicator_raw (
    id bigserial PRIMARY KEY,
    dataset_name text NOT NULL REFERENCES socio.source_dataset(dataset_name),
    territory_code text NOT NULL,
    indicator text NOT NULL,
    value numeric,
    period text NOT NULL,
    source_version bigint NOT NULL DEFAULT 1 CHECK (source_version > 0),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (dataset_name, territory_code, indicator, period)
);

CREATE TABLE IF NOT EXISTS socio.indicator_normalized (
    territory_code text NOT NULL,
    indicator text NOT NULL,
    period text NOT NULL,
    z_score numeric,
    normalized_value numeric,
    method text NOT NULL,
    source_version bigint NOT NULL DEFAULT 1 CHECK (source_version > 0),
    PRIMARY KEY (territory_code, indicator, period)
);

CREATE TABLE IF NOT EXISTS socio.composite_index (
    territory_code text NOT NULL,
    index_name text NOT NULL,
    value numeric NOT NULL,
    method text NOT NULL,
    source_version bigint NOT NULL DEFAULT 1 CHECK (source_version > 0),
    as_of timestamptz NOT NULL,
    PRIMARY KEY (territory_code, index_name)
);

CREATE INDEX IF NOT EXISTS ix_socio_indicator_lookup
    ON socio.indicator_raw (territory_code, indicator, period);
CREATE INDEX IF NOT EXISTS ix_socio_composite_rank
    ON socio.composite_index (index_name, value DESC, territory_code);

-- ---------------------------------------------------------------------------
-- Fail-closed RLS for new tenant-owned data
-- ---------------------------------------------------------------------------

-- New tables have NOT NULL tenant_id and are safe to protect immediately.
-- A missing app.tenant_id produces no matches; the request context must be set
-- by the authenticated service boundary before tenant data can be read/written.
DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'tenant_config', 'tenant_config_history', 'lead_activity',
        'upload_blob', 'import_job', 'import_row', 'idempotency_key'
    ] LOOP
        EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE core.%I FORCE ROW LEVEL SECURITY', table_name);
    END LOOP;

    FOREACH table_name IN ARRAY ARRAY[
        'fact_signal', 'daily_rollup', 'source_efficiency_rollup',
        'pipeline_time_rollup', 'top_source_rollup', 'processed_event'
    ] LOOP
        EXECUTE format('ALTER TABLE pulse.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE pulse.%I FORCE ROW LEVEL SECURITY', table_name);
    END LOOP;

    FOREACH table_name IN ARRAY ARRAY['record_digest', 'single_flight_lock', 'processed_event'] LOOP
        EXECUTE format('ALTER TABLE brief.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE brief.%I FORCE ROW LEVEL SECURITY', table_name);
    END LOOP;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'core'
          AND tablename = 'tenant_config'
          AND policyname = 'tenant_config_tenant_isolation'
    ) THEN
        CREATE POLICY tenant_config_tenant_isolation ON core.tenant_config
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'core'
          AND tablename = 'tenant_config_history'
          AND policyname = 'tenant_config_history_tenant_isolation'
    ) THEN
        CREATE POLICY tenant_config_history_tenant_isolation ON core.tenant_config_history
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'core'
          AND tablename = 'lead_activity'
          AND policyname = 'lead_activity_tenant_isolation'
    ) THEN
        CREATE POLICY lead_activity_tenant_isolation ON core.lead_activity
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'core'
          AND tablename = 'upload_blob'
          AND policyname = 'upload_blob_tenant_isolation'
    ) THEN
        CREATE POLICY upload_blob_tenant_isolation ON core.upload_blob
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'core'
          AND tablename = 'import_job'
          AND policyname = 'import_job_tenant_isolation'
    ) THEN
        CREATE POLICY import_job_tenant_isolation ON core.import_job
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'core'
          AND tablename = 'import_row'
          AND policyname = 'import_row_tenant_isolation'
    ) THEN
        CREATE POLICY import_row_tenant_isolation ON core.import_row
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'core'
          AND tablename = 'idempotency_key'
          AND policyname = 'idempotency_key_tenant_isolation'
    ) THEN
        CREATE POLICY idempotency_key_tenant_isolation ON core.idempotency_key
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
END $$;

DO $$
DECLARE
    table_name text;
    policy_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'fact_signal', 'daily_rollup', 'source_efficiency_rollup',
        'pipeline_time_rollup', 'top_source_rollup', 'processed_event'
    ] LOOP
        policy_name := table_name || '_tenant_isolation';
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'pulse'
              AND tablename = table_name
              AND policyname = policy_name
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

    FOREACH table_name IN ARRAY ARRAY['record_digest', 'single_flight_lock', 'processed_event'] LOOP
        policy_name := table_name || '_tenant_isolation';
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'brief'
              AND tablename = table_name
              AND policyname = policy_name
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON brief.%I
                 USING (tenant_id::text = current_setting(''app.tenant_id'', true))
                 WITH CHECK (tenant_id::text = current_setting(''app.tenant_id'', true))',
                policy_name,
                table_name
            );
        END IF;
    END LOOP;
END $$;

-- CONTRACT prerequisites for a later migration:
-- 1. Backfill tenant_id on legacy core.company, lead, project, signal, location,
--    lead_outcome, audit_log, and outbox_event rows.
-- 2. Set app.tenant_id in every transaction after validating the JWT tenant.
-- 3. Audit cross-tenant references and verify tenant-scoped query plans.
-- 4. Add strict policies and enforce non-null tenant ownership on legacy tables.
-- 5. Retire analytics/socioeconomic only after pulse/socio parity is verified.
