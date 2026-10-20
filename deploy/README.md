# Deployment

`docker-compose.yml` is the F0 local environment. It runs APIs and outbox/
stream workers separately so the complete flow is observable without another
supervisor. Staging and production keep a private network and expose only the
gateway. Vercel frontend settings, Caddy, Bright Data credentials, and Supabase
must be configured outside the repository through environment secrets.

The Python image shares `libs` and service code to keep the first cut small.
Once service boundaries stabilize, build contexts can be separated without
changing contracts.

## Local database and data UI

The local Postgres is built from `deploy/Dockerfile.postgres`: `postgres:16` plus pgvector, PostGIS
and pg_cron, the extensions `ops/bootstrap_roles.py` enables. It keeps the normal `postgres`
superuser, so migrations and the role bootstrap run exactly as in the F0 plan. pg_cron is preloaded
on the command line and pinned to `POSTGRES_DB`.

An existing `postgres_data` volume was created by the old Alpine image. Alpine and Debian sort text
differently, so run `make invent-tracker-reset` once before using the new image.

`make db-ui` starts pgAdmin (profile `ui`, not part of `make invent-tracker`) at
http://localhost:5050 (`PGADMIN_PORT` changes the port). It listens on loopback only and has no
pgAdmin login. The server "Invent Market Tracker (local)" is already registered; pgAdmin asks for
the `postgres` password (`POSTGRES_PASSWORD` in `.env`) the first time. The role is a superuser, so
queries see every tenant and bypass row level security. Use it for inspection, not for application
code. The data is under Databases, then the application database, then Schemas (`ingest`, `core`,
`pulse`, `brief`, `ml`, `socio`, `identity`).
