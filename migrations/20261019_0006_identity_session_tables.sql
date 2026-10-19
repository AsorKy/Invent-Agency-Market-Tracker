-- Expand-only migration: identity.credential and identity.auth_session, the two
-- tables Django migration iam.0001 creates (services/identity/iam/models.py).
-- Relates to REQ-IDN-001 and REQ-DAT-001. Columns match the Django models; the
-- parity is checked by tests/unit/test_identity_schema.py.
--
-- Both statements use IF NOT EXISTS, so a database where Django already created
-- the tables keeps them unchanged. Django still tracks iam.0001 in its own
-- migration state; apply the SQL first or fake that migration.

CREATE TABLE IF NOT EXISTS identity.credential (
    user_id uuid PRIMARY KEY,
    password_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS identity.auth_session (
    key_hash varchar(64) PRIMARY KEY,
    user_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    csrf_token varchar(64) NOT NULL,
    created_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    revoked_at timestamptz
);

-- Access pattern: list or revoke every session of one user (logout everywhere).
CREATE INDEX IF NOT EXISTS ix_auth_session_user_id
    ON identity.auth_session (user_id);

-- Grants for the identity service role. The role may not exist in local Compose
-- (the migration runs as postgres), so the grant is conditional.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'identity_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON identity.credential, identity.auth_session TO identity_app;
    END IF;
END $$;

-- Row level security: auth_session is the only table with tenant_id. The policy
-- applies when the connection sets app.tenant_id. The identity service resolves
-- a cookie by key_hash before the tenant is known, so a connection that leaves
-- app.tenant_id unset keeps full access. FORCE is not set for the same reason.
ALTER TABLE identity.auth_session ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'identity'
          AND tablename = 'auth_session'
          AND policyname = 'auth_session_tenant_isolation'
    ) THEN
        CREATE POLICY auth_session_tenant_isolation ON identity.auth_session
            USING (
                coalesce(current_setting('app.tenant_id', true), '') = ''
                OR tenant_id::text = current_setting('app.tenant_id', true)
            )
            WITH CHECK (
                coalesce(current_setting('app.tenant_id', true), '') = ''
                OR tenant_id::text = current_setting('app.tenant_id', true)
            );
    END IF;
END $$;
