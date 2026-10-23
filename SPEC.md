# Invent Market Tracker - Specification

Status: ACCEPTED v3.1 (2026-10-18). Derived from ADR-IMT-0001-ARCH v2.3, ADR-IMT-0002-LENS v1.1, and ADR-IMT-0003-PRED v1.2 in `docs/adr/`, all three `accepted` on 2026-10-18. Requirements are `accepted`; run 1 implements the `layer: L1`, `scope: run1` ones listed in `TASKS.md`.

This file turns the ADRs into requirements that agents can implement and verify. It is the middle layer of the source-of-truth chain defined in `AGENTS.md`: ADRs, then this file, then `TASKS.md`, then code.

## How to use this file

- Every requirement has a stable ID: `REQ-<AREA>-<NNN>`. IDs are never reused.
- Every requirement states its layer, phase, scope, source (an ADR section), how to verify it, and the tasks that cover it.
- `layer`: `L1` (ADR-0001, backend base), `L2` (ADR-0002, analytics), `L3` (ADR-0003, predictive).
- `phase`: for L1, the roadmap phase F0 to F6 of ADR-0001 section 21. For L2 and L3, `R2` and `R3` (the run that implements them).
- `scope`: `run1` (implement in the current run, verifiable offline with mocks and datagen), `deferred` (specified for L1 but needs an external service or the frontend, so it is not part of run 1), `later-run` (L2 or L3, implemented only when that run opens).
- Every task in `TASKS.md` cites at least one requirement ID. Every requirement with `layer: L1`, `scope: run1`, and `status: accepted` must be cited by at least one task. `ops/check_traceability.py` (task T-002) enforces both rules.
- `status` values: `proposed` (not yet confirmed by the owner), `accepted`, `deprecated`. The ADRs behind these requirements are `accepted`.
- An agent in run 1 implements `layer: L1` and `scope: run1` requirements first and must satisfy the extension points `REQ-EXT-*`, so that later layers need only additive changes. `later-run` requirements of layer L2 are implemented only by the continuation tasks (T-100 and later in `TASKS.md`), which start after T-080 is `done` and only while budget remains. Layer L3 is never started in the same session.

## 1. Scope and layers

The product is built in three layers, one per run. Each layer sits on the previous one and may only add to it (new migrations in expand form, new event types, new JSONB keys, new modules).

| Layer | ADR | What it adds | Run | Where it lives |
| --- | --- | --- | --- | --- |
| L1 | ADR-IMT-0001-ARCH v2.3 | Backend base: ingestion, data model, 7 services plus Streamlit, events, security, CI/CD, base reporting | 1 (current) | all services, `libs/`, `migrations/`, `transform/`, `orchestration/` |
| L2 | ADR-IMT-0002-LENS v1.1 | Analytics layer: descriptive, diagnostic, and predictive lenses, deterministic funnel (gates G1-G7), validation V1-V6 | 2 | `core` (funnel and `validation` schema), `pulse`, `ml` workers, `libs/domain/metrics`, Streamlit page 9 |
| L3 | ADR-IMT-0003-PRED v1.2 | Predictive layer: scoring models, forecasting, ICP pyramid, NLP ladder, graphs, experiment protocol, territorial extension | 3 | `ml`, `pulse`, `socio`, `libs/domain`, `docs/experiments` |

### 1.1 End state of the whole product

When all three layers are done, the platform:

- Ingests company and signal data from Bright Data snapshots (Google Maps, Computrabajo, LinkedIn, Instagram, others), resolves every company against RUES, and quarantines unresolved ones.
- Stores data in one Supabase Postgres project with 8 schemas and 14 roles, with one owning service per schema.
- Runs 7 services plus Streamlit, communicating through a transactional outbox, per-consumer inbox, and Redis Streams.
- Serves operational KPIs (Pulse), a cached per-lead digest (Brief), a lead pipeline with a controlled state machine, imports as background jobs, and multi-tenant access with five roles.
- Scores leads with a permanent rule baseline, a calibrated supervised model once at least 200 outcomes exist, and uplift targeting with a permanent holdout.
- Applies a deterministic funnel (G1 to G7) to projects and validates them with financial, unit-economics, funnel, growth, portfolio, and meta-ML metrics.
- Forecasts volumes with intervals, matches leads and zones through the ICP pyramid, and ranks territories with the zonal opportunity index.
- Governs every analytical component with a pre-registered experiment sheet and an ADOPT, ITERATE, or KILL decision.
- Shows freshness, degradation, and quality metadata on every card, in Streamlit and (later) in the Next.js app.
- Ships through an 11-stage CI/CD pipeline with smoke tests, automatic rollback, and verified encrypted backups.
- Starts with one command (`make invent-tracker`), runs on mock data out of the box, and switches to live Bright Data and other sources by editing `.env` only, with real data reaching the public API that the frontend consumes.

### 1.2 Current run (run 1, layer L1)

Goal of run 1: a consolidated, tested backend that the owner can start with `make invent-tracker`, that shows seeded demo data through the public API, and that turns to live data when credentials are added to `.env`.

In scope now: every `L1` requirement marked `scope: run1`. Execution order follows phases F0, F1, F2, then F3. The sandbox has no Docker, Supabase, Bright Data, or RUES, so run 1 proves behavior with unit tests, fakes, and recorded fixtures. The owner then runs `make invent-tracker`, `make test-integration`, and `make verify-live` on a machine with Docker and credentials.

Delivered by run 1 and activated later by the owner:

- The live Bright Data connector, with polling fallback, written against the documented Datasets API and tested with recorded fixtures (REQ-ING-004, REQ-ING-008). Credentials and dataset IDs are supplied through `.env` (REQ-RUN-004).
- The public API and OpenAPI contract that the frontend consumes (REQ-API-001 to REQ-API-003).

Continuation: if the budget is not exhausted when T-080 (the run 1 review pack) is `done`, the session continues with the layer L2 tasks T-100 to T-112 in `TASKS.md`, in dependency order. They implement ADR-IMT-0002-LENS (requirements `REQ-LNS-*`). The session stops at the end of the L2 tasks or when the budget runs out, whichever comes first. Layer L3 (ADR-IMT-0003-PRED) is never started in this session.

Out of scope now:

- The frontend (`frontend/`), including the Next.js app, design tokens, and the visual identity ADR. Requirements for it exist only as `scope: deferred`.
- A live RUES connector (the interface and a mock are in scope), and live connectors for DANE and OSM.
- Deployment to Vercel, Hetzner, or Railway.
- Everything marked `later-run`.

## 2. Architecture invariants

These come from ADR-IMT-0001 section 3 and amendments E1 and E2. They apply to every change.

| ID | Layer | Invariant |
| --- | --- | --- |
| INV-01 | L1 | One schema has one owning service. No service reads another service's schema; it uses the API or consumes events. |
| INV-02 | L1 | n8n orchestrates only. No business logic in workflows. |
| INV-03 | L1 | Every relevant change in core writes its event to the outbox in the same transaction. |
| INV-04 | L1 | Consumers are idempotent through an inbox table (`processed_event`) keyed by event ID, one per consumer schema. |
| INV-05 | L1 | The gateway holds no database credentials and does not route `/internal/*` externally. |
| INV-06 | L1 | Migrations follow expand/contract. Destructive changes use a `contract_` migration in a separate commit. |
| INV-07 | L1 | Instants are stored as timestamptz in UTC. Money is never a float (numeric(14,2) in Postgres, decimal string in the API). |
| INV-08 | L1 | Degradation is per card: a failed dependency disables its card, not the page. |
| INV-09 | L1 | Freshness is visible: responses carry `as_of` and `stale`; scores carry `score_version`. A null numeric means no data, not zero. |
| INV-10 | L1 | RUES is the identity axis. A company without a RUES match after two attempts is quarantined and does not count as a lead. |
| INV-11 | L1 | Outcomes are captured from day one (`core.lead_outcome`). It is infrastructure, not a feature. |
| INV-12 | L1 | Only company data and persons in a public business role are stored. Never national ID numbers, private phones, home addresses, minors, or sensitive data. |
| INV-13 | L1 | When the base text and an amendment disagree, the higher-numbered amendment wins (E2 over E1 over base). |
| INV-14 | L2 | Every feature answers the five LENS questions (D, Dx, P, prescriptive gate, V metric with kill trigger) before it passes the G4 Design gate. |
| INV-15 | L2 | Every analytical output carries `as_of` or `measured_at` plus `source_version`, and the quality metrics of its lens. |
| INV-16 | L3 | Complex components are validated with a single simple statistic, never with another model. |
| INV-17 | L3 | Dictionary before model. A model is a measured fallback, not the default. |
| INV-18 | L3 | The rule-based score is a permanent baseline and is never switched off. |
| INV-19 | L3 | No analytical component enters production without a pre-registered experiment sheet and an ADOPT decision. |

## 3. Catalogs

### 3.1 Service catalog (end state)

Canonical source: ADR-IMT-0001 section E2.2. There is no `analytics` service (absorbed by `pulse`) and no `funnel` service (a logical layer inside `core`).

| Service | Tech | Schema | Layer | Responsibility | Owner role |
| --- | --- | --- | --- | --- | --- |
| gateway | FastAPI | none | L1 | Single public entry: JWT, CSRF, edge secret, rate limit, routing | lead-backend-engineer |
| identity | Django + DRF | `identity` | L1 | Users, roles, tenants, scopes, JWKS, service tokens, admin UI (internal only) | lead-backend-engineer |
| ingest | FastAPI | `ingest` | L1 | Webhooks, entity resolution, enrichment agent, CSV imports | second-backend-engineer |
| core | FastAPI | `core` (+ `validation` in L2) | L1, L2 | Companies, signals, leads, pipeline, projects, outcomes, outbox; funnel and validation in L2 | lead-backend-engineer |
| pulse | FastAPI | `pulse`, `socio` | L1, L2, L3 | Rollups, KPI endpoints, socioeconomic indicators, forecasts and attribution in L2 | second-backend-engineer |
| brief | FastAPI | `brief` | L1 | Per-lead digest with single-flight cache and stale-while-revalidate | lead-backend-engineer |
| ml | FastAPI | `ml` | L1, L3 | Segmentation, scoring, feature store; models and pyramid in L3 | second-backend-engineer |
| streamlit | Streamlit | none (stateless) | L1, L2, L3 | Reporting pages; reads Parquet and the gateway API | second-backend-engineer |
| mocks | Flask | none | L1 | Bright Data and RUES doubles for development and tests | qa-reviewer |

Repository state: all services exist as scaffolds. `services/analytics` must be merged into `services/pulse` and removed (REQ-ARC-001).

### 3.2 Schema catalog (end state)

Canonical source: ADR-IMT-0001 section E2.1. Run 1 creates 7 schemas and 14 roles. Run 2 adds the `validation` schema, operated by the `core` roles, for 8 schemas and 14 roles in total.

| Schema | Owner service | Roles | Layer | Main content |
| --- | --- | --- | --- | --- |
| `identity` | identity | `identity_owner`, `identity_app` | L1 | users, roles, tenants, scopes, keys |
| `ingest` | ingest | `ingest_owner`, `ingest_app` | L1 | raw_snapshot, failed_fetch, budget, source_registry, entity_candidate, entity_match_log, quarantine, rues_lookup_queue, processed_event |
| `core` | core | `core_owner`, `core_app` | L1 | company (SCD2), location, person_role, signal, lead, lead_outcome, lead_activity, pipeline_stage, project, tenant_config, lead_score_rule, upload_blob, job, outbox_event, outbox_dead_letter, audit_log, processed_event; L2 audit tables for gates and kill triggers |
| `pulse` | pulse | `pulse_owner`, `pulse_app` | L1 | fact_signal, fact_company_snapshot, fact_lead_outcome, daily_rollup, semantic_metric_definition, processed_event; L2 attribution, forecast |
| `brief` | brief | `brief_owner`, `brief_app` | L1 | record_digest, single_flight_lock, processed_event |
| `ml` | ml | `ml_owner`, `ml_app` | L1 | feature_store, company_segment, lead_score, model_version, score_history, single_flight_lock, processed_event; L3 pyramid and evaluation tables |
| `socio` | pulse (socio worker) | `socio_owner`, `socio_app` | L1 | source_dataset, indicator_raw, indicator_normalized, composite_index; L3 territory, territory_index |
| `validation` | core | core roles | L2 | financial_run, unit_economics, funnel_metric, growth_metric, portfolio_snapshot, meta_ml_metric |

The names `analytics` and `socioeconomic` are retired.

## 4. Requirements

Template entry:

```text
### REQ-<AREA>-<NNN> Short title
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-000X section N
- statement: one testable sentence.
- verify: command or test path that proves it.
- tasks: T-NNN
```

## 4.1 Layer L1 - ADR-IMT-0001-ARCH (backend base, run 1)

### Quality and traceability

### REQ-QA-001 Static checks pass on every commit
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: AGENTS.md quality gates, ADR-IMT-0001 section 17
- statement: `make static` passes on every commit pushed by an agent.
- verify: `make static`
- tasks: T-000, T-001, T-007, T-080, T-141, T-161, T-162, T-164, T-168, T-171, T-172, T-187, T-190

### REQ-QA-002 Unit test suite exists and runs offline
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section 17 stage 2
- statement: A unit test suite under `tests/unit/` runs with no network, database, or Redis, and is exposed through `make test`.
- verify: `make test`
- tasks: T-001, T-003, T-004, T-005, T-006, T-081, T-094

### REQ-QA-003 Coverage thresholds
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section 17 stage 2
- statement: Unit coverage is at least 80 percent overall and at least 90 percent for `libs/domain`.
- verify: `pytest --cov=libs --cov=services --cov-fail-under=80`
- tasks: T-065, T-126, T-127

### REQ-QA-004 Offline pipeline test
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 7, E1.9 (smoke S5, S7)
- statement: One test pushes a mock snapshot through webhook, processor, entity resolution, company upsert and signal creation, outbox relay, pulse rollup, ml score, auto-qualification, and brief, using in-memory fake repositories and fake Redis Streams, and asserts the final payloads of `/api/v1/leads`, `/api/v1/leads/{id}/brief`, and `/api/v1/pulse/*` served through the gateway application. It runs inside `make test`.
- verify: `pytest -q tests/unit/test_pipeline_flow.py`
- tasks: T-077, T-092

### REQ-QA-005 Integration test tier
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 17 (stage 3), 5.5
- statement: `make test-integration` runs `tests/integration` against Postgres and Redis (the Compose services or URLs given by environment variables): migrations apply to an empty database, role grants isolate schemas, the outbox relay publishes to real Redis Streams, and the flow of REQ-QA-004 produces rows in every expected schema. The tests carry the `integration` marker and are skipped when no database URL is configured, so `make test` stays offline. The owner runs this tier.
- verify: `pytest -q tests/integration --collect-only`
- tasks: T-078, T-095

### REQ-TRC-001 Spec to task traceability
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: this file
- statement: Every task cites existing requirement IDs, and every accepted F0 requirement is cited by a task.
- verify: `python ops/check_traceability.py`
- tasks: T-002, T-080, T-157, T-167

### Architecture and topology

### REQ-ARC-001 Service topology matches the catalog
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 sections E2.2, E2.4, E2.5
- statement: `docker-compose.yml`, `README.md`, settings, and `.env.example` describe exactly the services of section 3.1 (plus redis, postgres, n8n, caddy, and mocks in development). No `analytics` service, schema, route, or variable remains, and `services/analytics` is merged into `services/pulse`.
- verify: `python ops/check_configuration.py` (extend it to assert this) and `make compose-config`
- tasks: T-050, T-073

### REQ-ARC-002 Ownership registry covers every service
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section E2.5 item 7, decision D-04
- statement: `.codex/agent-registry.yaml` and the roster in `AGENTS.md` assign an owner role to identity, brief, pulse, streamlit, and mocks as listed in section 3.1.
- verify: a unit test that loads the registry and checks every directory under `services/` has an owner.
- tasks: T-016, T-156

### Data and migrations

### REQ-DAT-001 Schemas and roles match the ADR
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section E2.1
- statement: Migrations create exactly the 7 schemas and 14 roles of section 3.2 with consistent names across migrations, settings, compose, `.env.example`, and `ops/bootstrap_roles.sql.example`. `socioeconomic` is removed through a `contract_` migration in a separate commit, and `analytics` is never created.
- verify: `python ops/check_migrations.py`
- tasks: T-008, T-081, T-083, T-086, T-093, T-094, T-095, T-129, T-145, T-156

### REQ-DAT-002 Time and pagination conventions
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 amendment E1.3 and section 15
- statement: Shared helpers produce UTC millisecond instants and stable keyset cursors.
- verify: `pytest -q tests/unit/test_common.py`
- tasks: T-003

### REQ-DAT-003 Data taxonomy
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 amendment E1.3
- statement: Event instants are timestamptz and millisecond UTC numbers in APIs; dates without time use `date`; money is numeric(14,2) and a decimal string in APIs; semistructured data is JSONB; IDs are uuid v4 except `signal` (bigserial); aggregate versions are monotonic bigint; a `local_day(ts, city)` helper is the only way to group by local day.
- verify: `pytest -q tests/unit/test_common.py` plus a migration check that no `json`, `float`, or `real` column is declared.
- tasks: T-009

### REQ-DAT-004 Pooler-safe database drivers
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section 5.5
- statement: asyncpg uses `statement_cache_size=0` and `prepared_statement_cache_size=0` with pool_size 4, pre-ping, and timeout 5; psycopg 3 uses `prepare_threshold=None` with pool_size 2. Runtime uses the transaction pooler; migrations and backups use the session port.
- verify: `pytest -q tests/unit/test_db_settings.py` (asserts the driver arguments); the PgBouncer integration test `tests/integration/test_db_pooler.py` is marked `integration` and skipped offline.
- tasks: T-009

### REQ-DAT-005 Database security
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section 5.4
- statement: Row-level security is enabled on `core.company`, `core.lead`, `core.project`, `core.signal`, `core.location`, and `core.lead_outcome` by `tenant_id` (D-29); the infrastructure tables `core.outbox_event`, `core.outbox_dead_letter`, `core.processed_event`, `ml.processed_event`, and `core.audit_log` are exempt with a recorded reason; the Supabase Data API is not used; connections require SSL verification; the service_role key never reaches the frontend or the gateway.
- verify: `python ops/check_migrations.py` asserts `ENABLE ROW LEVEL SECURITY` and tenant policies for the three tables.
- tasks: T-018, T-082, T-093, T-094, T-095, T-125, T-132, T-143, T-191, T-192, T-195, T-196

### REQ-DAT-006 Extensions and roles bootstrap
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 sections 5.1, 5.4
- statement: The bootstrap enables `pgvector`, `postgis`, `pg_cron`, and `pg_trgm` idempotently and creates roles from a schema list, with `app` roles lacking DDL rights and no cross-schema grants.
- verify: `python ops/check_migrations.py`
- tasks: T-010, T-128

### REQ-DAT-007 Expand/contract guardian
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 sections 8.2, 17.3
- statement: The guardian fails a non-`contract_` migration containing DROP, RENAME, ALTER TYPE, or NOT NULL without default, and fails a `contract_` migration that arrives in the same commit as application code changes.
- verify: `python ops/check_migrations.py` plus unit tests with fixture migrations.
- tasks: T-010, T-093

### REQ-DAT-008 Deterministic synthetic data
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section 15.2
- statement: `python -m datagen build --city medellin --orgs 200 --days 180 --seed 42` is byte-for-byte reproducible and produces 200 companies across Medellin, Bogota, and Cali, about 1,500 signals over 180 days, and 50 leads with sample outcomes.
- verify: run the command twice and compare hashes of the output.
- tasks: T-013

### Gateway

### REQ-GW-001 Gateway health, readiness, and isolation
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 sections 6.4 and 14.1
- statement: The gateway serves `/health` and `/ready`, has no database URL in its settings, and returns 404 for paths outside the public API prefix.
- verify: `pytest -q tests/unit/test_gateway.py`
- tasks: T-004

### REQ-GW-002 Edge secret, CSRF, rate limit, request ID
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 sections 13.1, E1.8
- statement: `/api/v1/*` without the `X-Edge-Secret` header returns 404 (never 403). Write methods require a double-submit CSRF token (`__Host-csrf` cookie equals `X-CSRF-Token`) or return 403 `csrf_token_invalid`. A sliding-window limiter allows 60 requests per identity per 60 seconds by default. `X-Request-ID` is propagated downstream.
- verify: `pytest -q tests/unit/test_gateway.py`
- tasks: T-004

### REQ-GW-003 JWT verification and service routing
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 6.4, E1.2, E1.10
- statement: The gateway verifies user JWTs with the JWKS published by identity, caches the JWKS for 10 minutes, applies per-route timeouts, and returns 503 only for routes whose upstream `/ready` is red (polled every 10 seconds).
- verify: `pytest -q tests/unit/test_gateway_auth.py`
- tasks: T-032

### Identity

### REQ-IDN-001 Identity service and RBAC
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section E1.1
- statement: The identity service (Django and DRF) manages users, tenants, scopes by city and service line, and five roles (`admin`, `strategist`, `intake`, `bd_rep`, `analyst`) with the permissions of E1.1. It publishes a JWKS and issues service tokens. Its Django admin is reachable only on the internal network, never through the gateway.
- verify: `pytest -q tests/unit/test_identity_rbac.py`
- tasks: T-030, T-083, T-179

### REQ-IDN-002 Session lifecycle
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 13.1, E1.1
- statement: Login sets `HttpOnly`, `Secure`, `SameSite=Strict` cookies with the `__Host-` prefix. A session renews while active, expires after 30 minutes of inactivity and after 12 hours absolute, and logout invalidates the cookie. No token is stored in localStorage.
- verify: `pytest -q tests/unit/test_identity_session.py`
- tasks: T-031

### REQ-IDN-003 Tenant isolation
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 5.4, E1.9 (smoke S9)
- statement: A user of tenant B requesting a lead of tenant A receives 404. `intake` and `bd_rep` see only the cities and service lines assigned to them.
- verify: `pytest -q tests/unit/test_tenant_isolation.py` with fakes, and smoke S9 against a deployed stack.
- tasks: T-032

### Ingest and orchestration

### REQ-ING-001 Raw snapshot ingestion and change detection
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 sections 4.1, 4.3
- statement: The webhook stores each snapshot in `ingest.raw_snapshot` with its sha256 and `UNIQUE(source, external_id, sha256)`. Only changed content produces new signals, and an `ingest.processor` job is enqueued per snapshot.
- verify: `pytest -q tests/unit/test_ingest_snapshot.py`
- tasks: T-040

### REQ-SEC-002 Signed Bright Data webhook with replay window
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 amendment E1.1
- statement: The ingest webhook rejects requests with an invalid signature or a timestamp outside the configured replay window.
- verify: `pytest -q tests/unit/test_ingest_webhook.py`
- tasks: T-006

### REQ-ING-002 Budget and failure handling
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section 4.3
- statement: `ingest.budget(source, period, max_records, consumed)` blocks triggers beyond the monthly cap. Failed fetches are stored in `ingest.failed_fetch`, retried with exponential backoff, and moved to dead letter after 5 failures.
- verify: `pytest -q tests/unit/test_ingest_budget.py`
- tasks: T-041

### REQ-ING-003 Entity resolution against RUES
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 sections 3 (P3), 5.3
- statement: A candidate with match score above 0.9 against RUES by NIT updates the existing company; a score between 0.6 and 0.9 goes to `ingest.entity_match_log` for manual review; otherwise a new company is created and queued in `ingest.rues_lookup_queue`. A company without a RUES match after two attempts goes to `ingest.quarantine` and is not counted as a lead.
- verify: `pytest -q tests/unit/test_entity_resolution.py`
- tasks: T-044, T-089

### REQ-ING-004 Source connectors, mock and live
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 sections 4.1, 4.4, E1.1
- statement: `ingest` defines a `SourceConnector` interface (trigger a collection, fetch a snapshot, normalize a record). A mock implementation (backed by `services/mocks`) and a Bright Data implementation (Datasets API: trigger with a notify URL, progress, snapshot download, Bearer token) are selected by `INGEST_MODE`. Provider fields map to canonical fields through versioned mapping files in `libs/domain/source_mappings/<source>.yaml`, so a field change needs a file edit, not code. Sources: maps, computrabajo (F1), linkedin_companies, linkedin_jobs, instagram (F2). The RUES lookup has an interface and a mock only.
- verify: `pytest -q tests/unit/test_connectors.py` using `httpx.MockTransport` and recorded fixtures under `tests/fixtures/`; no test touches the network.
- tasks: T-042, T-043, T-048, T-096, T-098

### REQ-ING-005 Imports as background jobs
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section E1.7
- statement: `POST /api/v1/imports/{entity}` (companies, leads, activities) with an `Idempotency-Key` returns 202 with a job ID. `GET /api/v1/jobs/{id}` reports status and counters. Limits: 10 MB per file, 100,000 rows per job, 3 active jobs per tenant (the fourth returns 429). Rows are upserted by `row_sha256`, events are published per changed aggregate, and the upload blob is deleted when the job ends.
- verify: `pytest -q tests/unit/test_imports.py`
- tasks: T-026

### REQ-ING-006 Enrichment agent contract
- status: accepted
- layer: L1
- phase: F3
- scope: run1
- source: ADR-IMT-0001 sections 4.2, E1.1
- statement: A deep-enrichment request is queued by `ingest`, rate limited with a daily budget, and runs through a replaceable agent interface (mock in run 1). A provider failure sets the enrichment to `pending_retry` with backoff and surfaces as a Brief warning, never as a 500.
- verify: `pytest -q tests/unit/test_enrichment.py`
- tasks: T-046

### REQ-ING-007 Bright Data mock
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section E1.1
- statement: `services/mocks` emulates snapshots, latency, and failure rate (configurable) and signs webhooks like the real provider.
- verify: `pytest -q tests/unit/test_mocks.py`
- tasks: T-042, T-098

### REQ-ING-008 Polling fallback without a public webhook
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 sections 4.1, 4.3
- statement: When `INGEST_POLL_ENABLED=true` (default in the local profile), each triggered collection is tracked in `ingest.pending_snapshot` and polled until ready, so ingestion works without a public webhook URL. A snapshot delivered by both webhook and polling is stored once (REQ-ING-001).
- verify: `pytest -q tests/unit/test_ingest_poll.py`
- tasks: T-043

### REQ-ING-009 From snapshot to company and signal
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 sections 4.1, 5.3, E2.1
- statement: After entity resolution, the ingest processor upserts the company and creates signals in `core` only through core's internal API with the service token, using the mapping files of REQ-ING-004. A record that fails mapping or resolution is stored in `ingest.failed_record` with its reason and does not block the rest of the batch.
- verify: `pytest -q tests/unit/test_ingest_to_core.py`
- tasks: T-045, T-089, T-120, T-195, T-196

### REQ-ORC-001 n8n workflows are orchestration only
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 sections 2, 4.1
- statement: Versioned workflow JSON under `orchestration/n8n/` contains schedules, HTTP calls, and notifications only. It has no code nodes with business logic and no secrets.
- verify: `python ops/check_configuration.py` (extend it to scan workflow JSON for code nodes and secret-like values).
- tasks: T-017, T-047, T-049, T-133

### Core

### REQ-COR-001 Core domain model
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 sections 5.1, E2.1
- statement: `core` owns company (SCD2), location, person_role, signal, lead, lead_outcome, lead_activity, pipeline_stage, project, tenant_config, and audit_log. Every aggregate carries a monotonic `version`. The Brief digest is not stored in `core`.
- verify: `python ops/check_migrations.py` plus `pytest -q tests/unit/test_core_models.py`
- tasks: T-018, T-085, T-144, T-145, T-165

### REQ-COR-002 Lead state machine and optimistic concurrency
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section E1.1
- statement: Lead states follow Discovered, Qualified, Hot, Contacted, Meeting, Proposal, Won, with Lost reachable from Hot onward. `PATCH /leads/{id}/status` requires `expected_version` and returns 409 with the current state on a mismatch. Lead creation requires an `Idempotency-Key`.
- verify: `pytest -q tests/unit/test_lead_state.py`
- tasks: T-021

### REQ-COR-003 Outcomes from day one
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section 3 (P7)
- statement: Every contact, response, meeting, close, and spam mark is stored in `core.lead_outcome` and emits `lead.outcome_recorded`.
- verify: `pytest -q tests/unit/test_outcomes.py`
- tasks: T-022

### REQ-COR-004 Tenant configuration with ETag
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections E1.1, E1.6
- statement: `core.tenant_config` stores JSONB validated by Pydantic. `PUT /admin/tenant-config` requires `If-Match` and returns 412 on a stale ETag. A change emits `tenant.config_updated`.
- verify: `pytest -q tests/unit/test_tenant_config.py`
- tasks: T-023

### REQ-COR-005 Lead activities and conversion to project
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section E1.1
- statement: Logging an activity writes `core.lead_activity` and emits `signal.activity_logged`. Converting a lead creates `core.project` and emits `signal.converted_to_project` in the same transaction.
- verify: `pytest -q tests/unit/test_lead_actions.py`
- tasks: T-022

### REQ-COR-006 Rule-based lead score
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section 10.1
- statement: A weighted rule (ICP fit, signal intensity, territory) produces `ml.lead_score` with `score_version`, `source_version`, and `stale`. The weights come from `core.lead_score_rule` and per-tenant configuration. The score for a given input is deterministic.
- verify: `pytest -q tests/unit/test_scoring.py`
- tasks: T-053

### REQ-COR-007 Company upsert API
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 sections 5.1, 5.3, E1.4
- statement: `POST /internal/core/companies/upsert` creates or updates a company by NIT or by canonical key (name, city, source IDs), keeps history (SCD2), bumps `version`, and emits `company.updated` (or the creation event) in the same transaction. Repeating the same payload changes nothing.
- verify: `pytest -q tests/unit/test_company_upsert.py`
- tasks: T-020

### REQ-COR-008 Automatic qualification from scores
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections E1.1, E1.6
- statement: `core` consumes `lead.score_updated` and creates the lead (Discovered) or advances it to Qualified or Hot using `tenant_config.brief_rules` (`min_score_for_hot`, default 70, and `min_score_for_qualified`, default 50). It never moves a lead backward and never overrides a human-set status. Each automatic move writes an `audit_log` entry and new leads emit `lead.created`.
- verify: `pytest -q tests/unit/test_auto_qualification.py`
- tasks: T-024, T-161, T-162, T-168, T-171

### Events and workers

### REQ-EVT-001 Transactional outbox with at-least-once relay
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section 7
- statement: Core persists a signal and its outbox event atomically, and the relay publishes unpublished events to Redis Streams without losing any on restart.
- verify: `pytest -q tests/unit/test_outbox.py`
- tasks: T-005, T-090, T-091, T-092, T-159, T-164, T-166, T-176

### REQ-EVT-002 Idempotent consumers
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section 7.4 and amendment E1.4
- statement: Processing the same event ID twice produces the effect once.
- verify: `pytest -q tests/unit/test_outbox.py`
- tasks: T-005, T-090, T-092, T-121, T-149, T-152

### REQ-EVT-003 Dead letter queue
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section 7.5
- statement: After 5 retries with backoff of 10 s, 30 s, 90 s, 5 min, and 15 min, an event moves to `core.outbox_dead_letter` with its error and an alert is raised.
- verify: `pytest -q tests/unit/test_outbox.py`
- tasks: T-025, T-091

### REQ-EVT-004 Republish
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section E1.4
- statement: `python -m core.republish --target <pulse|brief|ml> --since <date>` replays outbox events with `replay: true`, requires explicit confirmation, and blocks writes to the target while running. Consumers stay idempotent through the inbox.
- verify: `pytest -q tests/unit/test_republish.py`
- tasks: T-025

### REQ-EVT-005 Event catalog and envelope
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 sections 7.3, E1.4, E2.3
- statement: `libs/contracts/events.py` defines the L1 events (`signal.created`, `company.updated`, `lead.created`, `lead.outcome_recorded`, `lead.score_updated`, `ingest.quarantine`, `tenant.config_updated`, `signal.activity_logged`, `signal.converted_to_project`) with a common envelope (`event_id`, `event_type`, `aggregate_type`, `aggregate_id`, `version`, `source_version`, `created_at`, optional `replay`, and `tenant_id`, which the relay always publishes as its own stream field and never leaves to the payload). Event logs never include full payloads.
- verify: `pytest -q tests/unit/test_contracts.py`
- tasks: T-011, T-014, T-088, T-090, T-120, T-121, T-159, T-174

### REQ-EVT-006 Process supervisor
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section 6.3
- statement: If any child process dies, the supervisor stops the others and exits with code 1. A child that does not touch its heartbeat file within 90 seconds is treated as dead.
- verify: `pytest -q tests/unit/test_supervise.py`
- tasks: T-012, T-091

### REQ-EVT-007 Graceful shutdown
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section 14.3
- statement: Each service stops accepting requests, waits up to 25 seconds for active ones, flushes the outbox relay, acknowledges the current consumer batch, and closes pools.
- verify: `pytest -q tests/unit/test_lifespan.py`
- tasks: T-062, T-124

### REQ-EVT-008 Typed chain event payloads
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.6
- statement: Each chain event (`signal.tagged`, `signal.scored`, `company.matched`, `signal.explained`, `project.pipeline_updated`, `project.forecast_updated`) has a typed payload in `libs/contracts/events.py` with `extra="forbid"` and a `schema_version`; the envelope stays `DomainEvent` (`version`, `source_version`). `parent_event_id` is mandatory in the payload (a UUID or null). Canonical payloads: tagged `{signal_id, company_id, tags{source, signal_type}, parent_event_id}`; scored `{signal_id, company_id, score, score_version, parent_event_id}`; matched `{company_id, lead_id, rues_matched, parent_event_id}`; explained `{lead_id, company_id, qualification_reason, parent_event_id}`; pipeline_updated `{lead_id, project_id or null, from, to, version, parent_event_id}`; forecast_updated `{lead_id, project_id or null, stage, parent_event_id}`.
- verify: `pytest -q tests/unit/test_contracts.py`
- tasks: T-174, T-175, T-176, T-188

### Pulse, Brief, and ML (L1 part)

### REQ-PUL-001 Pulse endpoints and response rules
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section E1.5
- statement: `pulse` serves `/api/v1/pulse/unprocessed-signals`, `pipeline-backlog`, `source-efficiency`, `time-in-pipeline`, and `top-sources`. Every response includes `as_of` (UTC ms) and `stale` (true beyond 26 hours). A group with n below 5 sets `low_sample: true`. A null numeric means no data. The header is `Cache-Control: private, max-age=60`.
- verify: `pytest -q tests/unit/test_pulse_contract.py`
- tasks: T-051

### REQ-PUL-002 Incremental rollups
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 8.1, E1.1, E1.3
- statement: `signal.created` and `lead.outcome_recorded` update only the affected local day of `pulse.daily_rollup`. The daily job only closes the day. Day grouping uses `local_day(ts, city)` with the city's IANA zone.
- verify: `pytest -q tests/unit/test_rollups.py`
- tasks: T-052, T-088, T-152

### REQ-PUL-003 dbt star model and semantic layer
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 5.2, 8.1
- statement: The dbt project builds the dimensions (company, location, sector, service_line, source, date, territory) and facts (signal, company_snapshot, lead_outcome, lead_score, territory_indicator) in `pulse` as `mart_dim_<name>` and `mart_fact_<name>` (the `mart` component keeps them apart from the `fact_*` projection tables written by the pulse consumer), defines measures once in MetricFlow YAML, and materializes Parquet to the volume shared with Streamlit.
- verify: `dbt parse --project-dir transform`
- tasks: T-055, T-097


### REQ-PUL-004 Project stage projection
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.6
- statement: `pulse` consumes `project.pipeline_updated` and `project.forecast_updated` idempotently: it updates `pulse.project_snapshot` (stage) and closes the open `pulse.project_stage_visit` row (`exited_at`). The events reflect the stage transition of the lead; no project forecast model with value or burn exists yet (D-35).
- verify: `pytest -q tests/unit/test_project_projection.py`
- tasks: T-177

### REQ-BRF-001 Brief cache with stale-while-revalidate
- status: accepted
- layer: L1
- phase: F3
- scope: run1
- source: ADR-IMT-0001 section E1.6
- statement: `GET` brief compares the latest digest `source_version` with the expected version. First read regenerates synchronously, an outdated digest is served with `stale: true` while regeneration is queued, and the `X-Brief-Cache` header is `hit`, `stale`, or `degraded`. If `core` is unreachable the endpoint returns 503 `db_unavailable`.
- verify: `pytest -q tests/unit/test_brief_cache.py`
- tasks: T-033, T-085, T-086, T-087, T-122, T-151, T-165

### REQ-BRF-002 Single-flight regeneration and fallback
- status: accepted
- layer: L1
- phase: F3
- scope: run1
- source: ADR-IMT-0001 sections 10.2, E1.6
- statement: Regeneration takes a Redis lock (`SET NX PX 30000`, release by Lua only for the owner). If the semantic summarizer times out (5 s) or fails, a rule-based summarizer produces the digest with `degraded: true`.
- verify: `pytest -q tests/unit/test_brief_singleflight.py`
- tasks: T-033, T-087, T-122

### REQ-BRF-003 Per-tenant alert rules
- status: accepted
- layer: L1
- phase: F3
- scope: run1
- source: ADR-IMT-0001 section E1.6
- statement: Alert thresholds, weights, and disabled alert codes come from `tenant_config.brief_rules`. A `tenant.config_updated` event invalidates the tenant's older digests lazily. Alerts use the severities and codes listed in E1.1 (critical, warning, info).
- verify: `pytest -q tests/unit/test_brief_rules.py`
- tasks: T-034

### REQ-ML-001 Scoring service with single-flight
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 sections 10.2, 10.3
- statement: `ml` regenerates a company's score under a Redis single-flight lock. A changed `source_version` (company version plus signal versions) marks the stored score `stale` and queues regeneration. A locked company returns the previous score marked `stale`. The result is published as `lead.score_updated`.
- verify: `pytest -q tests/unit/test_ml_singleflight.py`
- tasks: T-053, T-144, T-145, T-158, T-165

### REQ-ML-002 Segmentation pipeline
- status: accepted
- layer: L1
- phase: F3
- scope: run1
- source: ADR-IMT-0001 section 10.1
- statement: A nightly batch builds firmographic features in `ml.feature_store` and segments companies with UMAP plus HDBSCAN (K-Prototypes with Gower as the comparison), storing `ml.company_segment` and a versioned `ml.model_version`. It runs on `datagen` data without network access.
- verify: `pytest -q tests/unit/test_segmentation.py`
- tasks: T-054

### REQ-ML-003 Semantic embeddings
- status: accepted
- layer: L1
- phase: F3
- scope: deferred
- source: ADR-IMT-0001 section 10.1
- statement: Descriptions are embedded with `multilingual-e5` into pgvector in the `ml` schema.
- verify: `pytest -q tests/unit/test_embeddings.py` with a stub encoder; the real model needs a download that is not available in the sandbox.
- tasks: none, deferred scope

### REQ-SOC-001 Socioeconomic indicators and opportunity index
- status: accepted
- layer: L1
- phase: F4
- scope: run1
- source: ADR-IMT-0001 sections 11, 4.4, E2.1
- statement: The `pulse` socio worker loads indicator files (municipality or comuna level, DANE-style CSV fixtures under `tests/fixtures/socio/`) into `socio.indicator_raw` and `socio.indicator_normalized`, computes z-scores within each city, and builds the composite indices IPET (economic power), IMD (digital maturity), and IOP = IPET high x IMD low x prospect density, with weights from PCA or entropy and a `method_version`. `GET /api/v1/territory/opportunity` returns the ranked territories with `as_of`, `method_version`, and `low_sample`. Live DANE, DNP, and OSM connectors are deferred; the loader reads files from a configured directory.
- verify: `pytest -q tests/unit/test_socio_indices.py`
- tasks: T-059, T-185

### REQ-ALR-001 Hot lead alert
- status: accepted
- layer: L1
- phase: F5
- scope: run1
- source: ADR-IMT-0001 sections 1.3, 4.1, 21 (F5 exit criterion)
- statement: When a lead becomes Hot, `core` posts one alert to the n8n webhook in `HOT_LEAD_WEBHOOK_URL` (forwarded to Telegram, mock locally) within 60 seconds of the commit that made it Hot, once per lead and version. A failed post is retried with backoff and never blocks the commit.
- verify: `pytest -q tests/unit/test_hot_alert.py` with a fake clock and a fake webhook.
- tasks: T-035

### Streamlit reporting

### REQ-STR-001 Multipage app and card contract
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 9, E1.5
- statement: `services/streamlit` has `Home.py` and pages 1_Market, 2_Leads, 3_Segmentos, 4_Territorios, 5_Pipeline, 6_Proyectos, 7_Ops. Each card loads through `safe_fetch(url)`, shows `as_of` and a stale warning, and on failure shows an error with a retry button without affecting other cards. Filters use `session_state` and `st.query_params`. Pages check the role in `session_state`. Exports are visible only to `admin` and `analyst`.
- verify: `pytest -q tests/unit/test_streamlit_helpers.py` (helpers) and Streamlit `AppTest` smoke tests per page.
- tasks: T-056, T-057, T-058

### REQ-STR-002 Stateless container
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 9.4, E2.1
- statement: The Streamlit image has no database credentials. It reads Parquet from a shared volume and calls the gateway API. Its health check is `/_stcore/health` and the Caddy reference config proxies WebSocket upgrades.
- verify: `python ops/check_configuration.py` and `make compose-config`
- tasks: T-056, T-073

### REQ-STR-003 Theme sync
- status: accepted
- layer: L1
- phase: F2
- scope: deferred
- source: ADR-IMT-0001 section 9.1
- statement: `scripts/sync_streamlit_theme.py` regenerates `.streamlit/config.toml` from the frontend design tokens. It waits for the frontend design specification.
- verify: `python scripts/sync_streamlit_theme.py --check`
- tasks: none, deferred scope

### Security and compliance

### REQ-SEC-001 Log redaction
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 amendment E1.8
- statement: Structured logs mask document numbers and emails and never contain cookies, authorization headers, or secrets.
- verify: `pytest -q tests/unit/test_common.py`
- tasks: T-003

### REQ-SEC-004 Secrets handling
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section 13.2
- statement: No secret appears in the repository, logs, or images. Only `.env.example` (names, no values) is committed. The static stage runs a secret scanner.
- verify: `python ops/check_configuration.py` and `gitleaks detect` where available.
- tasks: T-015, T-017, T-084

### REQ-SEC-005 Zero trust between services
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section 6.4
- statement: Each service connects with its own `_app` role. Service-to-service calls carry a signed service token. Redis has ACL users `edge` (rate-limit keys only) and `data` (streams, cache). `/internal/*` is never routed by the gateway. Service tokens are signed with HMAC-SHA256 and one shared key (`INTERNAL_SERVICE_TOKEN`), live 60 seconds, and each route has an allow list of services. Per-service keys (one per issuer, rotatable) are planned L2 work (D-28); asymmetric tokens are rejected as not worth their cost at this team size. The caller names `seed` and `verify_live` are accepted only outside production.
- verify: `pytest -q tests/unit/test_internal_auth.py` and `python ops/check_configuration.py`
- tasks: T-060, T-082, T-123, T-130, T-131, T-140, T-144, T-146, T-147, T-150, T-151, T-153, T-158, T-160, T-162, T-163, T-165, T-168, T-170, T-179, T-183, T-184, T-185, T-189, T-191, T-193, T-194, T-195, T-197

### REQ-CMP-001 Habeas data compliance
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section 13.4 (formal ADR pending: ADR-IMT-0006-HABEAS)
- statement: Records are purged 12 months after the last signal. An opt-out request executes an idempotent delete in `core`, inside the tenant of the caller only: the same company in two tenants needs two requests (D-36). Stored persons are limited to public business roles. A processing-activities record exists in `docs/`.
- verify: `pytest -q tests/unit/test_retention.py`
- tasks: T-027, T-191, T-197

### Resilience, CI/CD, backups, operations

### REQ-RES-001 Health and readiness
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section 14.1
- statement: Every service exposes `/health` (liveness, always 200 while alive) and `/ready` (checks its database and Redis, 503 otherwise).
- verify: `pytest -q tests/unit/test_health.py`
- tasks: T-012

### REQ-RES-002 Failure matrix
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section E1.10
- statement: With fakes, a failed `ml` leaves Pulse working and marks the Brief `degraded`; a failed `pulse` leaves the Brief working; a failed `brief` leaves the rest of the lead page working; a failed Redis leaves Postgres reads and writes working with the rate limiter open and duplicate regeneration tolerated.
- verify: `pytest -q tests/unit/test_failure_matrix.py`
- tasks: T-061

### REQ-CI-001 CI runs the unit stage
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section 17
- statement: The CI workflow runs unit tests after static checks and blocks on failure.
- verify: `.github/workflows/ci.yml` contains a unit job that depends on the static job.
- tasks: T-007

### REQ-CI-002 Eleven-stage pipeline
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section E1.9
- statement: `ci.yml` defines the stages static, unit, integration, contracts (oasdiff breaking), e2e, images, deploy migrate, deploy backend, deploy frontend, deploy streamlit, and deploy smoke, in that order. Deploy stages are skipped when their environment secrets are absent.
- verify: `pytest -q tests/unit/test_ci_workflow.py` parses the workflow and asserts stage names and order.
- tasks: T-064

### REQ-CI-003 Smoke catalog and rollback
- status: accepted
- layer: L1
- phase: F1
- scope: deferred
- source: ADR-IMT-0001 sections 17.1, E1.9
- statement: `tests/smoke` implements cases S1 to S13 against a base URL and skips when none is set. A failed smoke run moves the `prod` tag back and redeploys.
- verify: `pytest -q tests/smoke --collect-only`
- tasks: none, deferred scope

### REQ-BAK-001 Verified encrypted backups
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section 18
- statement: The nightly workflow dumps each schema with its owner role, records sha256, restores into an ephemeral Postgres and checks migration versions and table counts, encrypts with `age`, and uploads only the encrypted file (7 days daily, 28 days Sunday). RPO and RTO per schema are documented in `ops/backups/README.md`.
- verify: `pytest -q tests/unit/test_backups.py` with fakes for pg_dump and age.
- tasks: T-063

### REQ-OBS-001 Structured logs and metrics
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section 19
- statement: Services emit one-line JSON logs with `request_id` propagated from the gateway to consumers. Each service exposes Prometheus metrics at `/internal/metrics`, which the gateway never routes.
- verify: `pytest -q tests/unit/test_logging.py`
- tasks: T-062, T-124

### REQ-OBS-002 Tracing and circuit breaker
- status: accepted
- layer: L1
- phase: F6
- scope: deferred
- source: ADR-IMT-0001 sections 19, 21 (F6)
- statement: Services emit OpenTelemetry traces propagated from the gateway, and calls between services use a circuit breaker with the retry policy of the shared helper.
- verify: `pytest -q tests/unit/test_circuit_breaker.py`
- tasks: none, deferred scope

### REQ-DEP-001 Local stack
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 sections 16, 21 (F0)
- statement: `docker compose config` is valid, every service has a Dockerfile target and a health probe, and each image builds from the same base conventions. Starting the stack requires Docker, which is not available in the sandbox, so only the configuration is verified.
- verify: `pytest -q tests/unit/test_compose.py` (parses the file and checks topology); `make compose-config` where the Docker CLI exists.
- tasks: T-073, T-153, T-154, T-158

### Run, operate, and go live

### REQ-RUN-001 One command starts the application
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: owner requirement 2026-10-18; ADR-IMT-0001 section 16
- statement: `make invent-tracker` is the single command that starts the whole application. It creates `.env` from `.env.example` when missing (generating local-only secrets without printing them), builds the images, runs migrations, starts every service and worker, loads demo data on the first run, waits until the gateway `/ready` is green, and prints the service URLs and next steps. Running it again is safe. `make invent-tracker-down` stops it and `make invent-tracker-reset` removes volumes after confirmation.
- verify: `make -n invent-tracker` lists the steps; `pytest -q tests/unit/test_make_targets.py` parses the Makefile. The owner runs it once with Docker.
- tasks: T-074, T-125, T-130, T-140, T-141, T-161, T-164, T-170, T-171, T-173, T-187, T-192

### REQ-RUN-002 Migration runner
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 sections 8.2, 17 (stage 7)
- statement: `ops/migrate.py` applies `migrations/*.sql` in filename order, one transaction per file, records each in `public.schema_migration` (version, checksum, applied_at), refuses a file whose checksum changed, and is idempotent. Compose runs it as a one-shot `migrate` service that every database-backed service waits for with `service_completed_successfully`. Local role passwords are generated into `.env`.
- verify: `pytest -q tests/unit/test_migrate.py` with a fake connection.
- tasks: T-071

### REQ-RUN-003 Demo data
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: ADR-IMT-0001 section 15.2
- statement: `make seed` (also run on the first start) loads the `datagen` dataset through the public code paths, never by writing into another service's schema, so the outbox, consumers, scores, rollups, and briefs produce data for every list endpoint. It is idempotent and tags its rows `source=demo`; `make seed-clear` removes them.
- verify: `pytest -q tests/unit/test_seed.py`
- tasks: T-072

### REQ-RUN-004 Mock to live switch
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: owner requirement 2026-10-18; ADR-IMT-0001 sections 4.1, 4.4
- statement: `INGEST_MODE=mock|live` selects the connector set. Each source (`maps`, `computrabajo`, `linkedin_companies`, `linkedin_jobs`, `instagram`, `rues`) has `SOURCE_<NAME>_ENABLED`, a dataset ID variable, and a credential variable, all listed in `.env.example` without values. Live mode with a missing credential fails fast at startup with a message that names the variable and never shows a value. Going live needs only editing `.env` and re-running `make invent-tracker`.
- verify: `pytest -q tests/unit/test_source_config.py`
- tasks: T-015, T-043, T-084

### REQ-RUN-005 Doctor command
- status: accepted
- layer: L1
- phase: F1
- scope: run1
- source: owner requirement 2026-10-18; secrets policy
- statement: `make doctor` reports, by variable name only, which credentials and settings are set, which sources are active and in which mode, the health of each service, and whether the configured webhook URL is reachable. It never prints a value.
- verify: `pytest -q tests/unit/test_doctor.py` plants a sentinel secret and asserts it never appears in the output.
- tasks: T-075, T-186

### REQ-RUN-006 Live verification command
- status: accepted
- layer: L1
- phase: F3
- scope: run1
- source: owner requirement 2026-10-18
- statement: `make verify-live SOURCE=<name>` triggers one bounded collection (at most 5 records) for a live source, follows it through ingest and core, and reports pass or fail per hop (trigger, snapshot, normalize, resolve, signal, score, API read). It refuses to run in mock mode.
- verify: `pytest -q tests/unit/test_verify_live.py` with fakes. The owner runs the command with real credentials.
- tasks: T-076, T-096, T-193

### REQ-DOC-001 Go-live runbook
- status: accepted
- layer: L1
- phase: F3
- scope: run1
- source: owner requirement 2026-10-18
- statement: `docs/runbooks/go-live.md` (English) explains where credentials go (names only), how to enable each source, how to expose the webhook (tunnel) or use the polling fallback, how to adjust field mappings, how to run `make doctor` and `make verify-live`, how data reaches the API, how to point the frontend at the gateway, and a troubleshooting table.
- verify: `pytest -q tests/unit/test_runbook.py` checks that every `SOURCE_*` and mode variable appears in both the runbook and `.env.example`.
- tasks: T-079, T-134, T-155, T-166, T-169, T-173, T-185, T-186

### Public API for the frontend

### REQ-API-001 OpenAPI contract
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 12.6, E1.9 (stage contracts)
- statement: `make openapi` writes `docs/api/openapi.json` from the gateway application, describing every public `/api/v1` route with request and response schemas, error bodies, and the headers `X-As-Of`, `X-Brief-Cache`, `ETag`, and `Idempotency-Key` where relevant. A test fails when a public route is missing from the document or the committed file is stale.
- verify: `pytest -q tests/unit/test_openapi.py`
- tasks: T-070, T-148

### REQ-API-002 Public route table
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 12.4, E1.1, E1.5, E2.2
- statement: The gateway exposes the endpoints the frontend screens need: `POST /auth/session`, `GET /me`, `POST /logout`; `GET /leads` (filters state, service_line, city, segment, min_score, cursor), `POST /leads`, `GET /leads/{id}`, `PATCH /leads/{id}/status`, `POST /leads/{id}/activities`, `GET /leads/{id}/brief`; `GET /pipeline/kanban`; `GET /companies`, `GET /signals`; `GET /projects`, `POST /projects`; `GET /pulse/*`; `GET /ops/health`; `GET /territory/opportunity`; imports and jobs; `GET` and `PUT /admin/tenant-config`. Routes of L2 and L3 resources return 404 `not_in_layer` until their run. No route points to `analytics`. Authorization answers are a contract: a wrong role answers 403 `forbidden`; a city or service line outside the caller's scope, or a resource of another tenant, answers 404 `not_found` (cross-scope access looks like a missing resource); a resource of a layer that is not launched answers 404 `not_in_layer`. The roles `bd_rep` and `intake` receive 403 on the funnel and validation routes (D-27).
- verify: `pytest -q tests/unit/test_gateway_routes.py` and `pytest -q tests/unit/test_openapi.py`
- tasks: T-032, T-148, T-180

### REQ-API-003 Local profile for a browser frontend
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 sections 13.1, E1.8
- statement: A `local` profile lets a browser app served from `FRONTEND_ORIGIN` call the gateway: CORS allowlist with credentials, session cookie names without the `__Host-` prefix and with `Secure=false`, and seeded development users (one per role) whose passwords are generated into `.env`. The `production` profile keeps the attributes of REQ-IDN-002. Both profiles are covered by tests.
- verify: `pytest -q tests/unit/test_profiles.py`
- tasks: T-015, T-032

### Extension points for L2 and L3

### REQ-EXT-001 Tolerant event registry
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section E2.3
- statement: `libs/contracts` lists the reserved L2 and L3 event names (`signal.tagged`, `signal.scored`, `company.matched`, `signal.explained`, `funnel.gate_decision`, `funnel.pivot_requested`, `funnel.project_killed`, `funnel.kill_trigger_event`, `project.pipeline_updated`, `project.forecast_updated`, `review.candidate`, `component.evaluated`) without handlers. A consumer that receives an unknown type acknowledges it and logs a single line.
- verify: `pytest -q tests/unit/test_contracts.py`
- tasks: T-011, T-014, T-149

### REQ-EXT-002 Open tenant configuration
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section E2.3
- statement: The `tenant_config` model accepts the reserved keys `funnel_rules` and `funnel_rules.geo_overrides` without a migration, and rejects unknown keys elsewhere.
- verify: `pytest -q tests/unit/test_tenant_config.py`
- tasks: T-023

### REQ-EXT-003 Schema list drives bootstrap and checks
- status: accepted
- layer: L1
- phase: F0
- scope: run1
- source: ADR-IMT-0001 section E2.1
- statement: Role bootstrap and `ops/check_migrations.py` read the schema list from one place. Adding the `validation` schema needs one list entry and one expand migration.
- verify: a unit test adds a fake schema to the list and expects the bootstrap and the guardian to accept it.
- tasks: T-010

### REQ-EXT-004 Analytics-ready responses and outcomes
- status: accepted
- layer: L1
- phase: F2
- scope: run1
- source: ADR-IMT-0001 section E2.3; ADR-IMT-0002 section 2.2; ADR-IMT-0003 section 2.7
- statement: Pulse responses accept an optional `method_version` (null in L1). `core.lead` carries `source`, `city`, `service_line`, and a nullable `holdout` flag. `core.lead_activity` records channel and source, so attribution and holdout analysis need no destructive migration.
- verify: `python ops/check_migrations.py` and `pytest -q tests/unit/test_pulse_contract.py`
- tasks: T-014, T-018, T-051

### Frontend (deferred)

### REQ-FE-001 Next.js ERP application
- status: accepted
- layer: L1
- phase: F5
- scope: deferred
- source: ADR-IMT-0001 sections 12, 13.3
- statement: The Next.js 15 application provides login, market dashboard, lead list, Lead Panel, pipeline Kanban, project view, territory view, and ops view, with design tokens, a CSP without `unsafe-inline`, WCAG 2.2 AA, and stable `data-testid` values. The visual identity comes from a separate frontend ADR that is not part of this repository yet.
- verify: Vitest, Playwright, and axe in CI once the frontend opens.
- tasks: none, deferred scope

## 4.2 Layer L2 - ADR-IMT-0002-LENS (analytics, run 2)

All L2 requirements are `later-run`. They are specified now so that run 1 leaves the right extension points (section 4.1, REQ-EXT-*). Do not implement them in run 1.

### REQ-LNS-001 Lens tagging and quality metrics
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 sections 2, 2.9
- statement: Every analytical endpoint and worker declares its lens (D, Dx, P, prescriptive, V) and publishes the quality metrics of that lens (freshness and completeness for D; ATE with CI, p-value, effect size for Dx; AUC, calibration, lift for P).
- verify: `pytest -q tests/unit/test_lens_contract.py`
- tasks: T-101, T-112

### REQ-LNS-002 Five-question checklist
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.9
- statement: A new analytical feature ships with a spec answering D, Dx, P, prescriptive gate, and V metric with kill trigger. A checker blocks a feature spec that leaves any answer empty.
- verify: `python ops/check_five_questions.py`
- tasks: T-101

### REQ-LNS-003 Descriptive aggregation (D1)
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.1
- statement: `pulse` computes percentiles p10, p50, p90, MAD, IQR, HHI, and Gini as response fields, plus the project descriptive metrics (pipeline size, score distribution, burn versus plan, pipeline value by service line, median time per funnel stage).
- verify: `pytest -q tests/unit/test_descriptive_metrics.py`
- tasks: T-102, T-135, T-152

### REQ-LNS-004 Profiling and topics (D2, D3)
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.1
- statement: `ml` workers run K-Prototypes profiling, Leiden communities, and centrality weekly, and tag text with regex, dictionaries, YAKE, and TF-IDF (LDA monthly), publishing results to `pulse`.
- verify: `pytest -q tests/unit/test_profiling.py tests/unit/test_topics.py`
- tasks: T-103, T-135, T-139, T-150, T-160

### REQ-LNS-005 Attribution (Dx1)
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.2
- statement: Attribution supports last-touch, first-touch, time-decay, and Markov (removal effect) models and writes `pulse.attribution`. Shapley attribution is allowed only after 100 labeled closes.
- verify: `pytest -q tests/unit/test_attribution.py`
- tasks: T-104, T-136

### REQ-LNS-006 Causal inference (Dx2)
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.2
- statement: Difference-in-differences, synthetic control, and propensity matching report ATE with a 95 percent interval, ATT and ATU, p-value, effect size, robustness checks, and an E-value. A result whose sign changes under robustness checks is reported as inconclusive.
- verify: `pytest -q tests/unit/test_causal.py`
- tasks: T-105

### REQ-LNS-007 Drift and explainability (Dx3, Dx4)
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.2
- statement: A nightly worker computes change points (CUSUM, EWMA) and PSI with thresholds below 0.1 stable, 0.1 to 0.25 watch, above 0.25 retrain, and alerts through n8n. SHAP drivers and counterfactuals are attached to the Brief on regeneration. `icp_fit`, `signal_strength`, and `territory_opportunity` have a deterministic baseline derivation (`source_version` `demo-1`, `libs/domain/score_features.py`) that is never switched off; any supervised definition enters as an L3 component with an experiment sheet and runs in shadow next to the baseline (D-23).
- verify: `pytest -q tests/unit/test_drift.py tests/unit/test_explain.py`
- tasks: T-106, T-137, T-138, T-151, T-153, T-160, T-165, T-169, T-178

### REQ-LNS-008 Metric pack library
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.3
- statement: `libs/domain/metrics` implements pure functions for AUC, PR-AUC, Brier, reliability table, lift@K, precision@K by subpopulation, NDCG@K, MAP, MRR, sMAPE, MASE, pinball loss, interval coverage, and Qini.
- verify: `pytest -q tests/unit/test_metrics_pack.py`
- tasks: T-100

### REQ-LNS-009 Deterministic funnel in core
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 sections 2.4, 2.7; ADR-IMT-0001 section E2.2
- statement: `core` evaluates gates G1 Discovery, G2 Qualify, G3 Validate, G4 Design, G5 Launch, G6 Scale, and G7 Sunset from versioned YAML checklists and returns ADVANCE, PIVOT, KILL, or WAIT. It emits `funnel.gate_decision`, `funnel.pivot_requested`, `funnel.project_killed`, and `funnel.kill_trigger_event` through the outbox and stores each decision with its inputs. There is no separate funnel service. Only a KILL decision raises `project.version`; ADVANCE, PIVOT, and WAIT stay in the gate audit log without a version (D-27).
- verify: `pytest -q tests/unit/test_funnel.py`
- tasks: T-109, T-146, T-154, T-155, T-163
- note: blocked by decision D-06 (checklist content per gate).

### REQ-LNS-010 Validation schema and metrics (V1 to V6)
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 sections 2.5, 2.7
- statement: The `validation` schema (operated by `core` roles) holds `financial_run`, `unit_economics`, `funnel_metric`, `growth_metric`, `portfolio_snapshot`, and `meta_ml_metric`, each with `source_version`, `project_id`, and `measured_at`. Workers compute NPV, IRR, payback, discounted payback, and EMV (V1); CAC, LTV, LTV:CAC, CAC payback, and gross margin (V2); conversion, win rate, cycle time, velocity, and coverage (V3); MRR, NRR, GRR, Rule of 40, burn multiple, and magic number (V4); horizon mix, HHI, and Sharpe-like ratio (V5); model utility, decision latency, PSI, precision@K, and funnel lift (V6).
- verify: `pytest -q tests/unit/test_validation_metrics.py`
- tasks: T-107, T-108, T-147, T-181
- note: until REQ-LNS-015 lands the funnel metrics are computed per service line, not per project. The V6 fields that need data owned by `ml` or `pulse` stay empty with a reason until REQ-LNS-016 lands (INV-01).

### REQ-LNS-011 Validation decision thresholds
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.5
- statement: Thresholds are configuration, not code constants. Defaults: advance to G4 when NPV p10 is above 0; IRR above the hurdle rate (15 to 25 percent); payback under 18 months (H1 software) or 24 months (H2); LTV:CAC below 1 means kill, 1 to 3 watch, 3 to 5 healthy; CAC payback under 12 months; pipeline coverage 3 to 4 times quota; burn multiple above 3 means kill; horizon mix target 60/30/10.
- verify: `pytest -q tests/unit/test_validation_thresholds.py`
- tasks: T-108, T-147, T-154, T-155

### REQ-LNS-012 Event chain with source_version
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.6
- statement: A `signal.created` traverses signal.tagged, signal.scored, company.matched, signal.explained, funnel.gate_decision, project.pipeline_updated, and project.forecast_updated. Every event carries `source_version` and a `parent_event_id` (REQ-EVT-008), and `signal.scored` leaves `ml` through its outbox like every other domain event, so one decision can be traced end to end: `trace_chain` closes the chain once the producers propagate the parent id (D-21). `project.pipeline_updated` and `project.forecast_updated` reflect the stage transition of the lead; Pulse projects them (REQ-PUL-004). No project forecast model with value or burn exists yet (D-35).
- verify: `pytest -q tests/unit/test_event_chain.py`
- tasks: T-110, T-149, T-159, T-175, T-176, T-177, T-188

### REQ-LNS-013 Validation page
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 sections 2.7, 5 (spawns)
- statement: Streamlit page `9_Validation.py` shows V1 to V6 per project with `measured_at`, `source_version`, and per-card degradation. V6 is also shown in `7_Ops.py`.
- verify: Streamlit `AppTest` for page 9.
- tasks: T-111, T-148

### REQ-LNS-014 Validation inputs API
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.5
- statement: `PUT /internal/core/projects/{id}/validation-inputs` (roles `strategist` and `admin`, tenant scoped, ETag as for `tenant_config`) validates and stores the object that V1, V2, V4, and V5 read in `core.project.metadata->'validation'`. A strategist writes it by hand; there is no automatic worker. Without input, each card answers empty with the reason `needs_strategist_input` (D-24).
- verify: `pytest -q tests/unit/test_validation_inputs.py`
- tasks: T-179, T-180

### REQ-LNS-015 Funnel metrics per project
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.7
- statement: `core.lead.project_id` (a nullable foreign key to `core.project`, added by an expand migration with an index) is filled when a lead is converted to a project, and the funnel queries of `validation_worker` filter by `project_id` instead of `service_line`. Until then the funnel metrics are per service line (D-26).
- verify: `pytest -q tests/unit/test_validation_worker.py`
- tasks: T-181

### REQ-LNS-016 Meta-ML metrics through events
- status: accepted
- layer: L2
- phase: R2
- scope: later-run
- source: ADR-IMT-0002 section 2.5
- statement: `ml` and `pulse` emit the inputs of `psi`, `model_utility`, and `decision_latency_days` (`drift_score`, `model_utility_run`, `decision_latency_event`) through their outbox, and a `core` worker aggregates them into `validation.meta_ml_metric`. No service reads another service's schema (INV-01). Until then those fields stay null with their reason (D-25).
- verify: `pytest -q tests/unit/test_meta_ml_events.py`
- tasks: T-182

## 4.3 Layer L3 - ADR-IMT-0003-PRED (predictive, run 3)

All L3 requirements are `later-run`. Phase values in the statements are the ADR roadmap phases.

### REQ-PRD-001 Rule baseline v0 (P1)
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 sections 2.1, 2.6
- statement: The weighted rule score stays active as a shadow baseline for the whole life of the product. Its band-by-observed-win-rate table is a plain GROUP BY. If the "hot" band does not beat 1.5 times the base rate for two quarters (two-proportion test, alpha 0.05), weights are recalibrated or the rule is retired for review.
- verify: `pytest -q tests/unit/test_rule_baseline.py`
- tasks: none, later run

### REQ-PRD-002 Calibrated LightGBM (P1)
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 sections 2.1, 2.6
- statement: Once at least 200 labeled outcomes exist, a LightGBM model with isotonic or Platt calibration runs as challenger against the rule. PSI per feature (watch 0.1, retrain 0.25) and a rolling monthly Brier score are tracked. If it does not beat the rule in lift@K for two quarters, scoring rolls back to the rule.
- verify: `pytest -q tests/unit/test_lgbm_scoring.py`
- tasks: none, later run

### REQ-PRD-003 Uplift modeling (P1)
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 sections 2.1, 2.6
- statement: An X-learner uplift model ranks who to contact. A permanent random holdout of 5 to 10 percent measures it. If uplift at the top 30 percent is not above 0 with a 95 percent interval, contact falls back to the flat score.
- verify: `pytest -q tests/unit/test_uplift.py`
- tasks: none, later run

### REQ-PRD-004 Forecasting (P2)
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 sections 2.1, 2.6
- statement: A StatsForecast ensemble (AutoARIMA, Theta, ETS) with hierarchical reconciliation by city and service line publishes P10, P50, and P90 with `as_of`. MASE above 1 for three months degrades the forecast to a moving average. Interval coverage is tracked.
- verify: `pytest -q tests/unit/test_forecast.py`
- tasks: none, later run

### REQ-PRD-005 ICP pyramid (P3)
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 section 2.4
- statement: A `ProfileICPQuery` descends level 3 (sectors, monthly), level 2 (archetypes by sector with K-Prototypes, weekly), and level 0 (companies as 128-dimension vectors in pgvector) with beam width 5, returns k=20 matches reranked by distance times score times expected uplift, and attaches a `path_narrative` to each match. Only RUES-matched companies are indexed. If conversion of the optimal profile does not beat a random same-sector control (two-sample proportion test), the system falls back to flat k-NN.
- verify: `pytest -q tests/unit/test_icp_pyramid.py`
- tasks: none, later run

### REQ-PRD-006 Learning to rank (P3)
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 section 2.1
- statement: An LGBMRanker orders the daily inbox of each BD rep and is evaluated with NDCG@K.
- verify: `pytest -q tests/unit/test_ranker.py`
- tasks: none, later run

### REQ-PRD-007 Monte Carlo simulation (P4)
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 sections 2.1, 2.6
- statement: A worker in `core` simulates TAM, SAM, SOM, adoption, and pricing to publish the NPV distribution (p10, p50, p90), probability of NPV above 0, and expected payback. At the end of a project's first year, simulated versus real error per assumption is stored; a systematic error above 50 percent on any assumption blocks use of the method at gates until priors are reviewed.
- verify: `pytest -q tests/unit/test_monte_carlo.py`
- tasks: none, later run

### REQ-PRD-008 NLP ladder
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 section 2.2
- statement: Tech stack, job title to CIIU, sentiment, and vacancy classification use versioned dictionaries in `libs/domain/dictionaries/*.yaml` (about 500 technologies), `rapidfuzz`, and lexicons first. A zero-shot fallback handles only undefined cases (under 10 percent) and is measured monthly with Cohen's kappa on about 50 labeled cases; kappa below 0.6 routes those cases to manual review. LDA (coherence c_v at least 0.45, BERTopic otherwise), YAKE, and TF-IDF cosine complete the ladder.
- verify: `pytest -q tests/unit/test_nlp_ladder.py`
- tasks: none, later run

### REQ-PRD-009 Graphs by level
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 section 2.3
- statement: Graphs N3 (sector-city), N2 (company), N1 (lead), and Z (zone) live in Postgres with recursive CTEs. A nightly batch computes degree, PageRank, Leiden communities, and Jaccard link prediction. There is no GNN before 100,000 companies and no Neo4j before 500,000 nodes. If centrality does not correlate with conversion for two quarters, the graph is demoted to informational context.
- verify: `pytest -q tests/unit/test_graphs.py`
- tasks: none, later run

### REQ-PRD-010 Experiment protocol
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 section 2.7
- statement: Every analytical component has a pre-registered `docs/experiments/EXP-NNN-<component>.md` with the seven elements of the template (hypothesis, baseline, contrast, power, thresholds, decision, re-evaluation). Each evaluation emits `component.evaluated` with the verdict (ADOPT, ITERATE, KILL) and `source_version`. Benjamini-Hochberg applies when several components are evaluated together. The G4 Design gate blocks a component without a sheet.
- verify: `python ops/check_experiments.py` and `pytest -q tests/unit/test_component_evaluation.py`
- tasks: none, later run

### REQ-PRD-011 One-statistic validators
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 section 2.6
- statement: `libs/domain/validators` implements PSI, ARI, NMI, Cohen's kappa, MASE, Brier, interval coverage count, two-proportion test, Spearman with bootstrap interval, trend slope with p-value, and Moran's I as pure functions.
- verify: `pytest -q tests/unit/test_validators.py`
- tasks: none, later run

### REQ-PRD-012 Territorial hierarchy and IOZ
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 sections 2.8.1, 2.8.2
- statement: `socio.territory` stores City, Comuna, Barrio, and H3 (resolution 8 to 9). Every company is geocoded to its H3 cell and comuna. `socio.territory_index` stores IOZ(zone, line) = Power x Demand x (1 - Saturation), z-scored within each city, with `method_version`. Moran's I and Spearman against win rate validate it; if both are insignificant for two quarters, IOZ is demoted to context.
- verify: `pytest -q tests/unit/test_ioz.py`
- tasks: none, later run

### REQ-PRD-013 Geo-adaptive funnel
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 section 2.8.3
- statement: Gate thresholds can be overridden per city or comuna in `tenant_config.funnel_rules.geo_overrides`, but only with a significant two-proportion test between the two geographies. Each override is audited with its evidence in the gate decision record.
- verify: `pytest -q tests/unit/test_geo_overrides.py`
- tasks: none, later run

### REQ-PRD-014 Zone nodes and zone lookalike
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 section 2.8.4
- statement: Zone nodes carry `ubicada_en`, `adyacente_a` (queen contiguity), and `similar_a` (Euclidean distance over estrato, commercial density, CIIU mix, and cadastral value) edges and support lookalike zones for expansion.
- verify: `pytest -q tests/unit/test_zone_graph.py`
- tasks: none, later run

### REQ-PRD-015 Territory dashboards
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 section 2.8.5
- statement: Pages `1_Market.py` and `4_Territorios.py` offer a City, Comuna, H3 selector, an IOZ choropleth with confidence encoding (low-sample zones are visibly weaker), and a bivariate Power by Saturation map with the quadrants gold, war, seed, and avoid. Endpoints return `as_of`, `method_version`, and `low_sample`.
- verify: Streamlit `AppTest` for both pages and `pytest -q tests/unit/test_territory_api.py`.
- tasks: none, later run

### REQ-PRD-016 Rejected approaches stay out
- status: accepted
- layer: L3
- phase: R3
- scope: later-run
- source: ADR-IMT-0003 section 2.9
- statement: The codebase contains no tabular deep learning, GNN below the volume threshold, heavy semantic NER, zero-shot as default, heavy embeddings where TF-IDF suffices, premature Neo4j, AutoML or extensive hyperparameter search, pure reinforcement learning for the enrichment agent, or model-validates-model checks.
- verify: reviewer checklist in the run report; `ops/check_dependencies.py` rejects the corresponding packages.
- tasks: none, later run

## 5. Decisions

An agent must not resolve open decisions. A task that depends on one stays `blocked` until the owner records the decision here.

| ID | Question | Decision | Status |
| --- | --- | --- | --- |
| D-01 | Final schema list and names | 7 schemas and 14 roles in L1 (identity, ingest, core, pulse, brief, ml, socio); `socio` is canonical and `socioeconomic` is retired; L2 adds `validation` operated by `core` (8 schemas, 14 roles in total). Source: ADR-IMT-0001 section E2.1. | closed 2026-10-18 |
| D-02 | Keep `analytics` or replace it with `pulse` plus `streamlit` | `analytics` is replaced: `pulse` absorbs it and `services/analytics` is merged into `services/pulse`. Source: ADR-IMT-0001 section E1.2 and E2.2. | closed 2026-10-18 |
| D-03 | Service count | 7 services plus Streamlit (gateway, identity, ingest, core, pulse, brief, ml); there is no `funnel` service. Source: ADR-IMT-0001 section E2.2 and ADR-IMT-0002 section 2.7. | closed 2026-10-18 |
| D-04 | Ownership of identity, brief, pulse, streamlit, mocks | identity and brief: lead-backend-engineer. pulse and streamlit: second-backend-engineer. mocks: qa-reviewer. Source: proposal in `AGENTS.md`, section 3.1 of this file. | closed 2026-10-18 |
| D-05 | Supabase tier and VPS provider for the MVP | Recommendation in ADR-IMT-0001 section 22: Supabase Free until F3 and Hetzner CPX21. Not decided. | open, does not block run 1 |
| D-06 | Checklist content for gates G1 to G7 | The mechanism is specified (REQ-LNS-009); the checklists are agency knowledge to be authored by the owner. The engine ships with clearly marked example checklists (`example: true`) that production refuses to load unless `FUNNEL_ALLOW_EXAMPLE_CHECKLISTS=true`. The owner must replace them with the real checklists of the agency and remove the marker before real use (business content, an owner task; `docs/runbooks/go-live.md` section 14). | open, owner task, does not block the L2 code |
| D-07 | Retention window of `core.outbox_event` | Default 90 days; run 1 uses the default. | open, default applies |
| D-08 | Version `tenant_config` history (SCD2) | Recommended yes. Run 1 stores a `version` and keeps the previous row in an audit table; full SCD2 waits for the decision. | open, default applies |
| D-09 | Semantic summarizer for Brief in F3 | Recommended multilingual-e5 plus rules. Run 1 ships the rule-based summarizer behind a replaceable interface. | open, default applies |
| D-10 | Job runner: Celery or arq | ADR recommends Celery (already used by `procs.toml`). Run 1 keeps asyncio workers and the supervisor and does not add Celery. | open, default applies |
| D-11 | Habeas data detail (ADR-IMT-0006-HABEAS) | Pending ADR. REQ-CMP-001 carries the minimum. | open |
| D-12 | Frontend visual specification | Pending separate ADR (ADR-IMT-0004-LOOK is `proposed`). Frontend is out of scope for run 1. | open, does not block run 1 |
| D-13 | Bright Data dataset IDs and sample payloads | The owner supplies dataset IDs through `.env`. Samples are optional: mappings stay `unverified` until `make verify-live` captures redacted records from the first real snapshot and `ops/verify_mappings.py` passes on them (T-096). Synthetic fixtures never count as verification. The owner captures the snapshots with `make verify-live` per source on a machine with real credentials (`ops/verify_mappings.py --capture` redacts up to three records per source into `tests/fixtures/brightdata/captured/`), and confirms the real Computrabajo dataset ID in `.env` (`SOURCE_COMPUTRABAJO_DATASET_ID`) before that source is enabled. Verification is the owner's responsibility; the mock keeps serving run 1. | open, owner input (dataset IDs and live captures) |
| D-14 | Identity framework | Django plus DRF (ADR-IMT-0001 section E1.2, decision E1-D2). If Django proves impractical in the sandbox after three focused attempts, mark the identity tasks `blocked`; do not substitute another framework without an owner decision. | closed 2026-10-18 |
| D-15 | Live RUES connector | No official API is available to the sandbox. Run 1 delivers the interface and a mock; a live adapter is added when the owner provides access details. | open, does not block run 1 |
| D-16 | Tenant on the event stream | The relay publishes `tenant_id` as its own stream field on every event; a row without tenant goes to the dead letter queue with `missing_tenant_id`. Consumers read the field first. See T-090. | closed 2026-10-18 |
| D-17 | Single outbox relay | The dead letter loop is the only relay. There is a single relay IMPLEMENTATION (shared code, `libs/messaging/relay.py`); one INSTANCE may run per schema that has an outbox (`core` and, since D-21, `ml`), each with its own unit of work. A second relay implementation or a parallel legacy loop is not allowed. `outbox_relay.main` delegates to the shared loop and the legacy loop is deleted. See T-091 and T-176. | closed 2026-10-18, clarified 2026-10-22 |
| D-18 | SQL consistency tests | Two tiers: offline parsing with `pglast` against the schema built from migrations (T-094), and PostgreSQL checks with psycopg 3 using `PREPARE` (T-095), run by the owner or in the sandbox when an embedded PostgreSQL starts. | closed 2026-10-18 |
| D-19 | Naming of pulse tables | Projections `fact_*`, rollups `*_rollup`, dbt staging `stg_<source>__<entity>`, dbt marts `mart_dim_*` and `mart_fact_*`. See T-097. | closed 2026-10-18 |
| D-20 | Typed chain event payloads | The six chain events have typed payloads in `libs/contracts/events.py` (`extra="forbid"`, `schema_version` per type, envelope `DomainEvent`), each with a mandatory `parent_event_id`. See REQ-EVT-008 and T-174. | closed 2026-10-22 |
| D-21 | Parent ids and `signal.scored` through the outbox | The contract carries `parent_event_id` and `ml` publishes `signal.scored` through an outbox, because a domain event outside the outbox breaks the outbox rule and `trace_chain` must close the chain (REQ-LNS-012). The gap by design is no longer accepted. This does not violate D-17: one relay implementation, one instance per outbox schema (see the clarification in D-17). `ml.outbox_event` is infrastructure: no RLS (D-29), owned by `ml_app`, same columns as `core.outbox_event`, written in the same transaction as the score; the `ml` relay instance reads only `ml.*` (INV-01). L2 work, it does not block go-live L1. See T-175 and T-176. | closed 2026-10-22 |
| D-22 | Project stage events | A lead stage change is `project.pipeline_updated` plus `project.forecast_updated`, with no figures; Pulse consumes both. See REQ-PUL-004 and T-177. | closed 2026-10-22 |
| D-23 | Scoring feature definition | The current derivation is frozen as the permanent audited baseline `demo-1` (INV-18) and is never deleted. The production definition is L3 work with an experiment sheet (INV-19), owned by second-backend-engineer, and runs in shadow next to the baseline. A formula change bumps `source_version` as `features-<major>.<minor>` by pull request, and a test pins the seed output. See T-178. | closed 2026-10-22 |
| D-24 | Validation inputs V1, V2, V4, V5 | A strategist writes `core.project.metadata->'validation'` by hand through a core route; there is no automatic worker. See REQ-LNS-014, T-179, and T-180. | closed 2026-10-22 |
| D-25 | Meta-ML metrics through events, not cross-schema reads | `psi`, `model_utility`, and `decision_latency_days` are fed by events from `ml` and `pulse` aggregated by a `core` worker (INV-01 forbids crossing schemas). Deferred to R2; null with a reason until then. See REQ-LNS-016 and T-182. | closed 2026-10-22 |
| D-26 | Funnel metrics per project | The model is per project: `core.lead.project_id`, filled on conversion, and per-project queries. L2 data model change, it does not block L1. Metrics are per service line until then. See REQ-LNS-015 and T-181. | closed 2026-10-22 |
| D-27 | Funnel decision versioning and 403 versus 404 | Only KILL raises `project.version`. A wrong role answers 403; a cross-scope or cross-tenant resource answers 404 `not_found`; a resource of a layer that is not launched answers 404 `not_in_layer`. The roles `bd_rep` and `intake` receive 403 on the funnel and validation routes. See REQ-LNS-009 and REQ-API-002. | closed 2026-10-22 |
| D-28 | Service token keying | The shared HMAC key (`INTERNAL_SERVICE_TOKEN`) is accepted now (60 second tokens, private network, allow list per service); per-service keys are planned L2 work and asymmetric tokens are rejected. `seed` and `verify_live` callers exist only outside production. See REQ-SEC-005, T-183, and T-184. | closed 2026-10-22 |
| D-29 | RLS exemptions (T-082) | `core.signal`, `core.location`, and `core.lead_outcome` get RLS. `core.outbox_event`, `core.outbox_dead_letter`, `core.processed_event`, `ml.processed_event` (messaging and idempotency infrastructure, read and written by the owning service role) and `core.audit_log` (append-only, read by an admin route with an explicit tenant filter) stay exempt with those reasons. Eight exemptions become five. The outbox tables of `ml` (`ml.outbox_event` and its dead letter table, created by T-176) join the exempt infrastructure tables with the same reason. See REQ-DAT-005, T-082, and T-176. | closed 2026-10-22 |
| D-30 | Migration baseline | No new baseline: the expand and contract chain stays, and `contract_20261018_0003` and `contract_20261020_0008` already retire the legacy schemas. | closed 2026-10-22 |
| D-31 | PostgreSQL major version | 16 during run 1. An upgrade to 17 is evaluated in a separate experiment under `docs/experiments` before F4, testing pgvector, PostGIS, pg_cron, and pg_trgm on the custom image. | closed 2026-10-22 |
| D-32 | Docker integration and e2e run | The owner (or qa-reviewer) runs the integration tier and the e2e on a machine with Docker and `make invent-tracker` before go-live and records the result in `docs/runs/RUN-golive-docker.md`. The run also exercises `make verify-live` inside the Compose network (T-186). A high priority prerequisite of go-live; it closes everything marked unverified for lack of Docker. See T-187. | closed 2026-10-22 |
| D-33 | Authentication of `wf_socioeco_refresh` | The workflow authenticates with a short-lived service token (scope `pulse` only) minted before each run, not with a static header credential. The mint step uses a dedicated, single-purpose credential held by n8n, distinct from `INTERNAL_SERVICE_TOKEN` (its own secret, minimal scope: it can mint tokens only for `pulse` and reach nothing else). `INTERNAL_SERVICE_TOKEN` never enters n8n, in any case. Fallback, only if the credential cannot be bounded without exposing the shared key: a rotated static header accepted by pulse (`pulse-internal-header`) with a documented rotation policy, and D-33 is reopened with the note that identity cannot issue a bounded mint credential without the shared key. The workflow stays inactive until one of the two works. See T-185. | closed 2026-10-22 |
| D-34 | Go-live owners and date | Technical owner: lead-backend-engineer. Data and credentials owner: the owner. Target date: not set yet; the owner writes it here and in `docs/runbooks/go-live.md` before go-live. | open, target date |
| D-35 | Project forecast model with figures | No project forecast model with value or burn exists in L2; `project.forecast_updated` only reflects the stage transition. A model with figures is a future decision. | open, R3 |
| D-36 | Retention and opt-out under RLS | Option A: the compliance routes become tenant-aware. The caller sends `X-Tenant-Id` on opt-out; the retention job lists tenants from identity (not `core.tenant_config`, which is under RLS) and sets the tenant context for each before purging. No cross-tenant role or RLS-bypass policy is added (D-29, REQ-SEC-005). Opt-out by NIT deletes only within the caller's tenant; the same company in two tenants needs two requests. Audit keeps only hashed identifiers plus action, counts, requested_via, timestamp and tenant_id. A gateway route for operator-initiated opt-out (admin role) is tracked as T-197; the seed and internal path covers run 1. See REQ-CMP-001, T-191, T-194, and T-197. | closed 2026-10-23 |
| D-37 | Scope of the core.signal unique key | Option B: the key becomes `(tenant_id, source, source_external_id)` through an expand migration, with a later contract migration that drops the global unique (INV-06). The global key leaks the existence of another tenant's data and, under RLS, makes `create_signal` raise on a cross-tenant `source_external_id` collision (REQ-DAT-005, REQ-SEC-005). Two tenants never share a signal row; no cross-tenant dedupe is needed while only one tenant exists, but the expand verifies that no row violates the new key and never deduplicates by deleting. See T-195 and T-196. | closed 2026-10-23 |

## 6. Definition of done per layer

### L1 (run 1)

- Every `layer: L1` and `scope: run1` requirement is `done` in `TASKS.md` with evidence, or `blocked` with a recorded reason.
- `make static` and `make test` pass, `make compose-config` passes where the Docker CLI exists, and `make test` covers at least 80 percent of `libs` and `services` (90 percent of `libs/domain`).
- `make test` includes the offline pipeline test (REQ-QA-004), so a reviewer can see one signal travel from a mock snapshot to the public API.
- The repository conforms to ADR-IMT-0001 section E2.5: 7 schemas, 14 roles, 7 services plus Streamlit, no `analytics`, no `socioeconomic`.
- All `REQ-EXT-*` requirements pass, so L2 and L3 need only additive changes.
- The owner can run `make invent-tracker` on a machine with Docker, open the gateway, and read seeded leads, briefs, and pulse metrics through `/api/v1` with the local profile.
- The owner can add credentials to `.env`, run `make doctor` and `make verify-live`, and see live records arrive in `/api/v1/leads` without code changes (REQ-RUN-004 to REQ-RUN-006, `docs/runbooks/go-live.md`).
- `docs/api/openapi.json` is current.
- `docs/runs/RUN-<run-id>.md` lists tasks done and blocked, commands, assumptions (including every `unverified` field mapping), changed files, and unresolved risks.

### L2 (run 2)

- The gate checklists exist as versioned YAML and the owner has replaced the example ones (decision D-06).
- All `layer: L2` requirements pass; the `validation` schema exists through an expand migration.
- Every L2 feature has its five LENS answers on file.

### L3 (run 3)

- All `layer: L3` requirements pass.
- Every component has an experiment sheet with a recorded ADOPT, ITERATE, or KILL decision.
- The rule baseline is still running in shadow next to any supervised model.

## 7. Change log

| Date | Change |
| --- | --- |
| 2026-10-18 | Initial template generated from the repository state and ADR-IMT-0001 v2.2.1. |
| 2026-10-18 | Rewritten against ADR-IMT-0001 v2.3, ADR-IMT-0002 v1.1, and ADR-IMT-0003 v1.2. Added layers L1 to L3, the end-state definition, the service and schema catalogs, requirements for all three layers, extension points for runs 2 and 3, and decisions D-01 to D-04 closed. Existing requirement IDs are unchanged. |
| 2026-10-18 | v3: ADRs accepted, all requirements `accepted`. Added run and go-live requirements (REQ-RUN-001 to 006, REQ-DOC-001), the public API for the frontend (REQ-API-001 to 003), the ingestion flow and live connector (REQ-ING-004 rewritten, REQ-ING-008, REQ-ING-009), company upsert and auto-qualification (REQ-COR-007, 008), and the offline and integration test tiers (REQ-QA-004, 005). Traceability now covers every accepted `L1` `run1` requirement. Decisions D-13 to D-15 added, D-14 closed. |
| 2026-10-18 | v3.1: decisions D-16 to D-19 and D-13 revised; REQ-EVT-005 adds `tenant_id`; REQ-PUL-003 fixes the `mart_` naming. Tasks T-090 to T-097 added in TASKS.md v4. |
| 2026-10-18 | v3.1: review against ADR-0001 found five gaps. Added REQ-SOC-001 (socioeconomic indices), REQ-ALR-001 (hot lead alert), REQ-OBS-002 (deferred); added the territory route to REQ-API-002; REQ-DEP-001 no longer needs a Docker CLI; D-06 no longer blocks the L2 code; defined the continuation from run 1 to the L2 tasks. |
| 2026-10-22 | Owner decisions on the 14 open points recorded as D-20 to D-35 and D-06 and D-13 updated. New requirements REQ-EVT-008, REQ-PUL-004, and REQ-LNS-014 to REQ-LNS-016; REQ-DAT-005, REQ-SEC-005, REQ-API-002, REQ-LNS-007, REQ-LNS-009, REQ-LNS-010, and REQ-LNS-012 amended. Tasks T-082 and T-174 to T-187 added in TASKS.md. |
| 2026-10-23 | Decisions D-36 (retention and opt-out under row level security) and D-37 (scope of the signal dedupe key) opened after the r1-n-rls report. Tasks T-191 (blocked by D-36), T-192 and T-193 added in TASKS.md. |
| 2026-10-23 | Owner closed D-36 (compliance routes become tenant-aware, option A) and D-37 (per-tenant signal key, option B). T-191 rewritten; T-194 (identity lists tenants), T-195 (expand) and T-196 (contract) added. REQ-CMP-001 states that opt-out acts only inside the caller tenant. |
