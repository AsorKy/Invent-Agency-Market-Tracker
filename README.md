# Invent Market Tracker

F0 scaffold for Invent's ERP-lite and Colombia-first market analytics platform.
The product evaluates SaaS, AaaS, consulting, digital marketing, event
catering, and related projects using market evidence, while curating leads for
the agency.

The implementation follows `ARCH-0001 v2` and starts with F0: five Python
services, a public gateway, Postgres with five schemas, Redis Streams, versioned
contracts, a transactional outbox, dbt, n8n workflows, and reproducible
synthetic data. The frontend is reserved for a later phase.

## Structure

```text
services/          gateway, ingest, core, analytics, ml, and mocks
libs/              configuration, database, Redis, contracts, and pure domain
migrations/        expand bootstrap for the five schemas
transform/         dbt project and initial semantic model
orchestration/n8n/ versioned workflows without secrets
datagen/           deterministic synthetic data for development
ops/               migrations, backups, and runbooks
deploy/             Dockerfile, Compose, and reference Caddy configuration
frontend/          future contract and integration point
```

## Local startup

```bash
cp .env.example .env
docker compose up --build
```

Available endpoints and ports:

- Gateway health: `http://localhost:8000/health`
- Gateway readiness: `http://localhost:8000/ready`
- n8n: `http://localhost:5678`
- Postgres: `localhost:5432`
- Redis: `localhost:6379`

To generate synthetic data without Bright Data:

```bash
python -m datagen build --city medellin --orgs 200 --days 180 --seed 42
```

To review the repository before a change:

```bash
make static
```

This command validates migrations, Python module documentation, syntax, Ruff,
and mypy. Operational scripts and workers document their behavior,
relationships, and purpose in their headers.

## Initial decisions

- The gateway has no Postgres credentials; it only routes to internal services.
- `core` writes `core.signal` and `core.outbox_event` in one transaction.
- The Core relay publishes events to Redis Streams with at-least-once delivery.
- F1 scoring is a deterministic and auditable rule. Supervised techniques wait
  for F6, when enough outcomes exist.
- Real Bright Data, RUES, DANE, and n8n connectors require external secrets and
  contracts, so this repository includes interfaces, workflows, and mocks
  without real data.
- Local migrations use the `postgres` user. Supabase deployments must create
  the ten roles in `ops/bootstrap_roles.sql.example` and apply per-service
  grants.

## Status

This cut is the F0 skeleton and initial contract set. The next functional
increment is F1: connect Bright Data and RUES, implement entity resolution, and
replace draft read endpoints with complete repositories and RLS-aware access.
