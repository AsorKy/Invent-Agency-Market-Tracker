# Ops experiment: Supabase image as the local engine, with Studio (T-128)

Run: 2026-10-20, GitHub Actions run 39503437901 on a disposable branch. The workflow is kept as
`deploy/experiments/supabase/spike-workflow.yml.txt` (inactive; copy it to `.github/workflows/` on a
scratch branch to repeat the run). Throwaway passwords only. Linux amd64 runners; the images also
publish arm64.

## Question

Can the local database be `supabase/postgres` instead of `deploy/Dockerfile.postgres`, with
Supabase Studio as the query UI, while `ops.migrate` and `ops/bootstrap_roles.py` run unchanged?
What happens when they run as the non-superuser `postgres` role that hosted Supabase gives?

## Results

| Check | PG 17.11.0.006 | PG 15.19.0.006 |
| --- | --- | --- |
| Ready after `docker run`, image pull included | about 32 s | about 28 s |
| `supabase_admin` is superuser | yes | yes |
| `vector`, `postgis`, `pg_cron`, `pg_trgm`, `pgcrypto` available | yes (0.8.2, 3.3.7, 1.6.4) | yes (0.8.2, 3.3.2, 1.6.4) |
| `pg_cron` preloaded, `cron.database_name` | yes, `postgres` | yes, `postgres` |
| `ops.migrate` as `supabase_admin` | ok | ok |
| `ops/bootstrap_roles.py` as `supabase_admin` | ok, 4 extensions enabled, 7 app roles, 7 schemas | same |
| `ops.migrate` as `postgres` (not superuser) | ok | ok |
| `ops/bootstrap_roles.py` as `postgres` (not superuser) | FAILS: `must be able to SET ROLE "identity_owner"` | FAILS: `must be member of role "identity_owner"` |
| postgres-meta answers SQL on our database | yes (`select count(*)` returned) | yes |
| Studio container starts and redirects (HTTP 307) | yes | yes |
| Studio SQL proxy by curl | rejected: needs the connection header the browser adds | same |

Not verified: the Studio SQL editor and table editor in a browser. The image is `supabase/studio`
2026.10.19 with `supabase/postgres-meta` v0.100.0; only the two checks above were automated.

## Findings

1. The Supabase image works as an engine for the superuser flow, on 15 and 17, with the four
   extensions of REQ-DAT-006 and no change to the repo scripts.
2. The hosted case needs a fix before go-live. `ALTER SCHEMA ... OWNER TO <schema>_owner` requires
   the executing role to be a member of that role. A superuser is exempt; `postgres` on Supabase is
   not. `ops/bootstrap_roles.py` should grant each owner role to the current user (with the SET
   option on PG 16 and later) right after creating it. Migrations already pass as `postgres`.
3. Studio runs without the rest of the Supabase stack, but this run could not confirm its SQL editor.
   Expect a browser check by the owner. It adds two containers (meta and studio, about 470 MB of
   images) next to the database.
4. Version difference: `deploy/Dockerfile.postgres` is PG 16; the Supabase tags tested are 15 and 17.
   SPEC does not pin the major version. The local engine and the hosted one should share it.

## Verdict

Keep `deploy/Dockerfile.postgres` as the default local engine and pgAdmin (`make db-ui`) as the data
UI. Offer Supabase as an optional engine only after the owner tries Studio in a browser and decides
the PG major version. Fix finding 2 independently: it blocks any non-superuser deployment.

## Follow-ups

- Task: make `ops/bootstrap_roles.py` work for a role without superuser (finding 2), verified with
  this workflow on the hosted-like case.
- Owner: open Studio against the experiment stack and confirm the SQL and table editors.
- Owner: choose the PG major version for local and hosted environments.
