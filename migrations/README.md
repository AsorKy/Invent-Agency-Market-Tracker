# Migrations

`20261017_0001_expand_initial_schemas.sql` creates the F0 expand baseline. All
future migrations must remain compatible with deployed code: add tables,
indexes, and optional columns first; destructive changes must use the
`contract_` prefix after old code is removed.

Local Compose runs the migration as `postgres`. Supabase applies it with owner
roles and only the permissions for each service schema. The role example is in
`ops/bootstrap_roles.sql.example`.

