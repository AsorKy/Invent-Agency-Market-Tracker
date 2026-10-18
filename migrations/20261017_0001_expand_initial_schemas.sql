-- Expand bootstrap for the five Invent Market Tracker schemas.
-- Creates tables for ingest, core, analytics, ml, and socioeconomic data, plus indexes,
-- outbox, processed_event, and initial RLS. Relates to services/*/app
-- repositories, dbt in transform/, and ops/check_migrations.py.
-- The file is mounted into local Postgres and must evolve through separate
-- contract_ migrations once persistent traffic exists.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS ml;
CREATE SCHEMA IF NOT EXISTS socioeconomic;

CREATE TABLE IF NOT EXISTS ingest.raw_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id text NOT NULL,
    dataset_id text NOT NULL,
    source text NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    fetched_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, snapshot_id, payload_sha256)
);

CREATE TABLE IF NOT EXISTS ingest.failed_fetch (
    id bigserial PRIMARY KEY,
    source text NOT NULL,
    external_id text,
    error text NOT NULL,
    attempts integer NOT NULL DEFAULT 1,
    next_attempt_at timestamptz,
    dead_lettered_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingest.budget (
    source text NOT NULL,
    period date NOT NULL,
    max_records integer NOT NULL,
    consumed integer NOT NULL DEFAULT 0,
    PRIMARY KEY (source, period)
);

CREATE TABLE IF NOT EXISTS ingest.source_registry (
    source text PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT true,
    cadence text NOT NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingest.entity_candidate (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_snapshot_id uuid NOT NULL REFERENCES ingest.raw_snapshot(id),
    candidate_key text NOT NULL CHECK (candidate_key ~ '^[0-9a-f]{64}$'),
    name text NOT NULL,
    city text,
    candidate_nit text,
    normalized_name text NOT NULL,
    resolution_attempts integer NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (raw_snapshot_id, candidate_key)
);

CREATE TABLE IF NOT EXISTS ingest.entity_match_log (
    id bigserial PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES ingest.entity_candidate(id),
    rues_nit text,
    score numeric(5,4) NOT NULL,
    decision text NOT NULL,
    reviewed_by text,
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingest.quarantine (
    id bigserial PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES ingest.entity_candidate(id),
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE TABLE IF NOT EXISTS core.company (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid,
    nit text,
    slug text NOT NULL UNIQUE,
    legal_name text NOT NULL,
    trade_name text,
    sector_code text,
    employee_count integer,
    website text,
    city text,
    source_flags jsonb NOT NULL DEFAULT '{}'::jsonb,
    version bigint NOT NULL DEFAULT 1,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_company_name_trgm ON core.company USING gin (legal_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_company_cursor ON core.company (updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS core.location (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES core.company(id),
    city text NOT NULL,
    department text,
    address text,
    latitude numeric(9,6),
    longitude numeric(9,6),
    h3_index text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.signal (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES core.company(id),
    source text NOT NULL,
    source_external_id text,
    signal_type text NOT NULL,
    observed_at timestamptz NOT NULL,
    strength numeric(5,4) NOT NULL DEFAULT 0.5,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_external_id)
);

CREATE INDEX IF NOT EXISTS ix_signal_company_observed ON core.signal (company_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS core.lead (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid,
    company_id uuid NOT NULL REFERENCES core.company(id),
    service_line text NOT NULL,
    stage text NOT NULL DEFAULT 'new',
    qualification_reason jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_lead_cursor ON core.lead (updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS core.lead_outcome (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id uuid NOT NULL REFERENCES core.lead(id),
    outcome text NOT NULL,
    notes text,
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.pipeline_stage (
    stage text PRIMARY KEY,
    ordinal integer NOT NULL,
    terminal boolean NOT NULL DEFAULT false
);

INSERT INTO core.pipeline_stage (stage, ordinal, terminal) VALUES
    ('new', 1, false), ('contacted', 2, false), ('meeting', 3, false),
    ('proposal', 4, false), ('won', 5, true), ('lost', 6, true)
ON CONFLICT (stage) DO NOTHING;

CREATE TABLE IF NOT EXISTS core.project (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid,
    name text NOT NULL,
    service_line text NOT NULL,
    hypothesis text NOT NULL,
    status text NOT NULL DEFAULT 'draft',
    estimated_roi numeric(12,2),
    actual_roi numeric(12,2),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.brief (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES core.project(id),
    content jsonb NOT NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.outbox_event (
    id bigserial PRIMARY KEY,
    event_id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    event_type text NOT NULL,
    version bigint NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);

CREATE INDEX IF NOT EXISTS ix_outbox_unpublished ON core.outbox_event (id)
    WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS core.outbox_dead_letter (
    id bigserial PRIMARY KEY,
    event_id uuid NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    error text NOT NULL,
    attempts integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.audit_log (
    id bigserial PRIMARY KEY,
    actor text,
    action text NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id uuid,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.processed_event (
    event_id uuid PRIMARY KEY,
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.fact_signal (
    signal_id uuid PRIMARY KEY,
    company_id uuid NOT NULL,
    source text NOT NULL,
    signal_type text NOT NULL,
    observed_at timestamptz NOT NULL,
    strength numeric(5,4) NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.fact_company_snapshot (
    snapshot_date date NOT NULL,
    company_id uuid NOT NULL,
    employee_count integer,
    city text,
    has_website boolean,
    PRIMARY KEY (snapshot_date, company_id)
);

CREATE TABLE IF NOT EXISTS analytics.fact_lead_outcome (
    outcome_id uuid PRIMARY KEY,
    lead_id uuid NOT NULL,
    outcome text NOT NULL,
    occurred_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.daily_rollup (
    rollup_date date NOT NULL,
    city text NOT NULL,
    service_line text NOT NULL,
    signal_count integer NOT NULL DEFAULT 0,
    lead_count integer NOT NULL DEFAULT 0,
    hot_lead_count integer NOT NULL DEFAULT 0,
    as_of timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (rollup_date, city, service_line)
);

CREATE TABLE IF NOT EXISTS analytics.territory_index (
    territory_code text PRIMARY KEY,
    ipet numeric(10,4),
    imd numeric(10,4),
    iop numeric(10,4),
    as_of timestamptz NOT NULL,
    source_version text NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_territory_iop
    ON analytics.territory_index (iop DESC NULLS LAST, territory_code);

CREATE TABLE IF NOT EXISTS analytics.semantic_metric_definition (
    metric_name text PRIMARY KEY,
    definition text NOT NULL,
    grain text NOT NULL,
    yaml_version text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.processed_event (
    event_id uuid PRIMARY KEY,
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ml.feature_store (
    company_id uuid PRIMARY KEY,
    features jsonb NOT NULL,
    source_version text NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ml.company_segment (
    company_id uuid PRIMARY KEY,
    segment_name text NOT NULL,
    cluster_id integer,
    model_version text NOT NULL,
    assigned_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ml.lead_score (
    company_id uuid PRIMARY KEY,
    score integer NOT NULL CHECK (score BETWEEN 0 AND 100),
    is_hot boolean NOT NULL DEFAULT false,
    stale boolean NOT NULL DEFAULT false,
    source_version text NOT NULL,
    score_version text NOT NULL,
    model_version text NOT NULL,
    as_of timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ml.score_history (
    id bigserial PRIMARY KEY,
    company_id uuid NOT NULL,
    score integer NOT NULL,
    source_version text NOT NULL,
    model_version text NOT NULL,
    as_of timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ml.model_version (
    version text PRIMARY KEY,
    model_type text NOT NULL,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ml.single_flight_lock (
    company_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS ml.processed_event (
    event_id uuid PRIMARY KEY,
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS socioeconomic.source_dataset (
    dataset_name text PRIMARY KEY,
    provider text NOT NULL,
    geography_level text NOT NULL,
    refreshed_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS socioeconomic.indicator_raw (
    id bigserial PRIMARY KEY,
    dataset_name text NOT NULL REFERENCES socioeconomic.source_dataset(dataset_name),
    territory_code text NOT NULL,
    indicator text NOT NULL,
    value numeric,
    period text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (dataset_name, territory_code, indicator, period)
);

CREATE TABLE IF NOT EXISTS socioeconomic.indicator_normalized (
    territory_code text NOT NULL,
    indicator text NOT NULL,
    period text NOT NULL,
    z_score numeric,
    normalized_value numeric,
    method text NOT NULL,
    PRIMARY KEY (territory_code, indicator, period)
);

CREATE TABLE IF NOT EXISTS socioeconomic.composite_index (
    territory_code text NOT NULL,
    index_name text NOT NULL,
    value numeric NOT NULL,
    method text NOT NULL,
    as_of timestamptz NOT NULL,
    PRIMARY KEY (territory_code, index_name)
);

ALTER TABLE core.company ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.lead ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.project ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'core' AND tablename = 'company'
          AND policyname = 'company_tenant_policy'
    ) THEN
        CREATE POLICY company_tenant_policy ON core.company
            USING (tenant_id IS NULL OR tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id IS NULL OR tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'core' AND tablename = 'lead'
          AND policyname = 'lead_tenant_policy'
    ) THEN
        CREATE POLICY lead_tenant_policy ON core.lead
            USING (tenant_id IS NULL OR tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id IS NULL OR tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'core' AND tablename = 'project'
          AND policyname = 'project_tenant_policy'
    ) THEN
        CREATE POLICY project_tenant_policy ON core.project
            USING (tenant_id IS NULL OR tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id IS NULL OR tenant_id::text = current_setting('app.tenant_id', true));
    END IF;
END $$;

