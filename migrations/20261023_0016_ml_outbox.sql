-- Expand: ml gets its own transactional outbox (T-176, D-21, REQ-LNS-012, REQ-EVT-001).
--
-- signal.scored is written to ml.outbox_event in the same transaction as the score, and the ml
-- relay instance publishes it to the event stream. Same columns as core.outbox_event and
-- core.outbox_dead_letter, in the ml schema only (INV-01: no grant on another schema). No row
-- level security: infrastructure read and written only by ml_app; tenant_id is routing metadata
-- (D-29). Additive and re-runnable; new tables are empty until the event chain flag is on.

CREATE TABLE IF NOT EXISTS ml.outbox_event (
    id bigserial PRIMARY KEY,
    event_id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id uuid,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    event_type text NOT NULL,
    version bigint NOT NULL,
    schema_version integer NOT NULL DEFAULT 1,
    payload jsonb NOT NULL,
    occurred_at timestamptz,
    request_id text,
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz,
    last_error text,
    claim_token uuid,
    claimed_at timestamptz,
    dead_lettered_at timestamptz,
    replay boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);

CREATE INDEX IF NOT EXISTS ix_ml_outbox_claimable
    ON ml.outbox_event (COALESCE(next_attempt_at, created_at), id)
    WHERE published_at IS NULL AND dead_lettered_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_ml_outbox_tenant_created
    ON ml.outbox_event (tenant_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS ml.outbox_dead_letter (
    id bigserial PRIMARY KEY,
    event_id uuid NOT NULL,
    tenant_id uuid,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    error text NOT NULL,
    attempts integer NOT NULL,
    source_outbox_id bigint,
    replay boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ml_outbox_dead_letter_tenant
    ON ml.outbox_dead_letter (tenant_id, created_at DESC, id DESC);

-- Grants for the ml service role only. The role may not exist in local Compose (the migration
-- runs as postgres), so the grant is conditional; bootstrap-roles also grants on all tables.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ml_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON ml.outbox_event, ml.outbox_dead_letter TO ml_app;
        GRANT USAGE, SELECT, UPDATE
            ON SEQUENCE ml.outbox_event_id_seq, ml.outbox_dead_letter_id_seq TO ml_app;
    END IF;
END $$;
