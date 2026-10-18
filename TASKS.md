# Invent Market Tracker - Tasks

Status: ACTIVE v3 (2026-10-18). Generated from `SPEC.md` v3 for run 1 (layer L1, ADR-IMT-0001). Task IDs are never reused.

Each task cites the requirements in `SPEC.md` that it satisfies. The run protocol (branch, commit, push, evidence, blocked handling) is defined in `AGENTS.md`. Only L1 work is listed; L2 and L3 tasks are generated when their runs open.

## What run 1 must deliver

- A tested backend (unit tests for every service and shared module, an offline pipeline test, and an integration tier for the owner to run) that is ready for review and promotion.
- `make invent-tracker` starting the whole application with seeded demo data.
- A switch to live data by editing `.env` only (`INGEST_MODE=live`, Bright Data credentials and dataset IDs), checked with `make doctor` and `make verify-live`, with records reaching the public API at `/api/v1` and the OpenAPI contract in `docs/api/openapi.json` for the frontend.

## Before launching (owner)

1. Optional but valuable: place 2 to 3 real sample responses per source under `tests/fixtures/brightdata/` (maps, computrabajo) so the field mappings are verified (decision D-13). Without them the agent marks mappings `unverified`.
2. Make sure the branch exists on GitHub and that the session has no real secrets (see `AGENTS.md`, sandbox assumptions).

## Task format

```text
### T-NNN Short title
- status: todo | in_progress | done | blocked
- spec: REQ-AREA-NNN, REQ-AREA-NNN
- owner: role id from .codex/agent-registry.yaml
- depends: T-NNN or none
- write_scope: paths the task may change
- context: files to read before starting (and nothing more)
- size: S | M | L
- acceptance: commands that must pass
- evidence: filled by the agent when done
```

Size is a rough token cost guide: S under 1 USD, M 1 to 3 USD, L 3 to 8 USD. Tests for a task live in its own `tests/unit/test_<area>.py` files; only T-001 edits `tests/conftest.py`. After T-014, edits to existing files in `libs/contracts/`, `libs/common/settings.py`, and `migrations/` need the coordinator; service tasks may add new contract modules.

## Run plan

| Run | Purpose | Tasks | Estimated cost |
| --- | --- | --- | --- |
| smoke | T-000 only | 1 tasks | about 1 USD |
| r1-a | Foundation: T-001 to T-018 (single agent, one role per task) | 18 tasks | about 38 USD |
| r1-b | Services: T-020 to T-059 (coordinator plus up to three lanes) | 34 tasks | about 107 USD |
| r1-c | Cross-cutting: T-060 to T-063 | 4 tasks | about 8 USD |
| r1-d | Launch and review: T-064, T-065, T-070 to T-080 | 13 tasks | about 29 USD |
| r2 | Continuation with ADR-0002: T-100 to T-112, only if budget remains after T-080 | 13 tasks | about 38 USD |

Total estimate for run 1 (smoke to r1-d): about 183 USD, plus about 38 USD for the optional ADR-0002 continuation, to be corrected after the smoke run shows real cost per task. Blocked tasks do not consume budget. Set the session budget of each run to about 1.5 times its estimate.

### Lanes in run r1-b

Multiagent work starts only after T-014 (contracts frozen). Use one coordinator plus at most three lanes with disjoint write sets; tasks inside a lane run in order.

| Lane | Tasks | Writes |
| --- | --- | --- |
| A (lead-backend-engineer) | T-020 to T-027, T-030 to T-035 | services/core, services/identity, services/gateway, services/brief |
| B (second-backend-engineer) | T-040 to T-049 | services/ingest, services/mocks, orchestration/n8n, libs/domain/source_mappings |
| C (second-backend-engineer) | T-050 to T-059 | services/pulse, services/analytics (delete), services/ml, services/streamlit, transform |

Cross-lane dependencies (for example T-045 needs T-020) are resolved by the coordinator: take the first task whose `depends` are all `done`.

## If the budget is not exhausted: continue with ADR-0002

The session does not stop when T-080 (the run 1 review pack) is `done`. If budget remains, it continues with the layer L2 tasks T-100 to T-112, which implement ADR-IMT-0002-LENS (`REQ-LNS-*`). They all depend on T-080, so the normal task loop reaches them in order. Rules:

- Start T-100 only when T-080 is `done`; never start an L2 task earlier, even if its own dependencies are met.
- Stop at the end of T-112 or when the budget runs out. Never start layer L3 (ADR-IMT-0003) in this session.
- The funnel engine (T-109) ships with example checklists only; the owner replaces them (decision D-06).
- The last section of the run report states whether the L2 tasks ran, which are `done`, and which were left `todo` for a later session.

## Tasks

### T-000 Smoke test
- status: todo
- spec: REQ-QA-001
- owner: coordinator
- depends: none
- write_scope: docs/runs/
- context: AGENTS.md
- size: S
- acceptance: `make static`
- note: create `docs/runs/RUN-smoke.md` with one line per check performed (git branch, commit, push, `make static` result). If `make static` cannot run because tools are missing, record exactly which ones. Also record the Python version and whether `pip install .[dev]` works.
- evidence:

### T-001 Test tooling and make targets
- status: done
- spec: REQ-QA-001, REQ-QA-002
- owner: coordinator
- depends: T-000
- write_scope: pyproject.toml, Makefile, tests/conftest.py, tests/unit/test_smoke.py, tests/integration/conftest.py
- context: pyproject.toml, Makefile, tests/README.md
- size: M
- acceptance: `make test` runs and exits 0 with the placeholder suite; `make static` still passes
- note: add `pytest`, `pytest-asyncio` (asyncio_mode auto), `pytest-cov`, and `fakeredis` to the `dev` extra with bounded versions. Add targets `test` (offline, excludes the `integration` marker), `test-integration` (only that marker), and `coverage`. Register the `integration` marker. `tests/conftest.py` provides: default environment so `get_settings()` works offline, an `asgi_client(app)` helper built on `httpx.ASGITransport` that also runs the app lifespan, a `fake_redis` fixture, and a documented pattern for in-memory fake repositories. Only this task edits `tests/conftest.py`; other tasks add `tests/unit/support_<area>.py` modules.
- evidence: `pytest -q -m "not integration"` -> 1 skipped (offline sandbox has no pydantic-settings, so the placeholder skips); `pytest -q -m integration` -> 1 deselected; `ruff check tests`, `ruff format tests`, language and script-doc guards OK. `make static` fails in this sandbox only on mypy (tool runs on Python 3.11, cannot parse PEP 695 in libs/common/retry.py).

### T-002 Spec to task traceability checker
- status: done
- spec: REQ-TRC-001
- owner: qa-reviewer
- depends: T-001
- write_scope: ops/check_traceability.py, tests/unit/test_traceability.py
- context: SPEC.md (section How to use this file), TASKS.md, ops/check_language.py
- size: M
- acceptance: `python ops/check_traceability.py` exits 0 on this repository and exits non-zero when a task cites an unknown requirement ID or when an accepted `layer: L1` `scope: run1` requirement has no task; `pytest -q tests/unit/test_traceability.py`
- note: the script needs a module docstring in the repository style. Parse `### REQ-` headings, their `- status:`, `- layer:`, and `- scope:` lines, and the `spec:` lines of tasks. Wiring into `make static` is done by T-017. Also print (without failing) any accepted `later-run` requirement that no task cites.
- evidence: `python ops/check_traceability.py` -> OK (119 requirements, 83 tasks); `pytest -q tests/unit/test_traceability.py` -> 9 passed; ruff, language, script-doc guards OK.

### T-003 Unit tests for shared helpers
- status: done
- spec: REQ-QA-002, REQ-SEC-001, REQ-DAT-002
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_common.py
- context: libs/common/pagination.py, libs/common/retry.py, libs/common/time.py, libs/common/log_redaction.py
- size: M
- acceptance: `pytest -q tests/unit/test_common.py`
- note: cover cursor round trip and ordering, retry attempts and bounded backoff, UTC millisecond helpers, and redaction of document numbers, emails, and secret-bearing keys. Report production defects instead of changing production code.
- evidence: `/tmp/py12 -m pytest -q tests/unit/test_common.py` -> 37 passed (python3.12 with pytest only; offline sandbox)

### T-004 Gateway tests
- status: todo
- spec: REQ-QA-002, REQ-GW-001, REQ-GW-002
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_gateway.py
- context: services/gateway/app/main.py, libs/common/settings.py, libs/common/security.py
- size: M
- acceptance: `pytest -q tests/unit/test_gateway.py`
- note: use the in-process ASGI client and fake upstreams. No Redis or Postgres. Cover `/health`, `/ready` with a failing upstream, 404 for non-public paths, edge secret enforcement (404, never 403), double-submit CSRF (403 `csrf_token_invalid`), the rate limiter with the fake Redis, and `X-Request-ID` propagation. Report production defects instead of changing production code.
- evidence:

### T-005 Outbox relay and idempotent consumer tests
- status: todo
- spec: REQ-QA-002, REQ-EVT-001, REQ-EVT-002
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_outbox.py
- context: services/core/app/outbox_relay.py, services/core/app/worker.py, libs/common/redis_streams.py, libs/contracts/events.py
- size: L
- acceptance: `pytest -q tests/unit/test_outbox.py`
- note: use fakes for the database and Redis. Verify unpublished events are published once marked, retried on failure, and that a repeated event ID is applied once by each consumer (ml and the pulse consumer under its current name).
- evidence:

### T-006 Ingest webhook signature tests
- status: todo
- spec: REQ-QA-002, REQ-SEC-002
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_ingest_webhook.py
- context: services/ingest/app/main.py, libs/common/security.py, libs/common/settings.py
- size: M
- acceptance: `pytest -q tests/unit/test_ingest_webhook.py`
- note: cover a valid signature, a wrong signature, a stale timestamp, and a future timestamp outside the window.
- evidence:

### T-007 Add the unit job to CI
- status: todo
- spec: REQ-QA-001, REQ-CI-001
- owner: devops-engineer
- depends: T-003, T-004, T-005, T-006
- write_scope: `.github/workflows/ci.yml`
- context: .github/workflows/ci.yml, Makefile
- size: S
- acceptance: the workflow file parses as YAML and contains a `unit` job with `needs: static` that runs `make test`
- evidence:

### T-008 Reconcile schema and role names
- status: done
- spec: REQ-DAT-001
- owner: database-engineer
- depends: T-001
- write_scope: migrations/, ops/bootstrap_roles.sql.example, tests/unit/test_schema_catalog.py
- context: migrations/README.md, migrations/20261017_0001_expand_initial_schemas.sql, migrations/20261017_0002_arch_v22_foundations.sql, ops/check_migrations.py, SPEC.md (section 3.2)
- size: M
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_schema_catalog.py`
- note: D-01 is closed in `SPEC.md`: 7 schemas (identity, ingest, core, pulse, brief, ml, socio) and 14 roles. Follow expand/contract: add the missing names in an expand migration; remove `socioeconomic` and never create `analytics` through a `contract_` migration in a separate commit. The test parses the SQL and the roles example and asserts the exact schema and role sets. Table additions are done by T-018, not here.
- evidence: `python ops/check_migrations.py` -> OK; `pytest -q tests/unit/test_schema_catalog.py` -> 5 passed, 1 xfailed (strict xfail records conflict: migration 0001 creates `analytics`, SPEC says never; see run report)

### T-009 Data taxonomy helpers and pooler driver settings
- status: done
- spec: REQ-DAT-003, REQ-DAT-004
- owner: lead-backend-engineer
- depends: T-001
- write_scope: libs/common/time.py, libs/common/db.py, libs/common/money.py, tests/unit/test_taxonomy.py, tests/unit/test_db_settings.py
- context: libs/common/time.py, libs/common/db.py, docs/adr/ADR-IMT-0001-ARCH.md sections E1.3 and 5.5
- size: M
- acceptance: `pytest -q tests/unit/test_taxonomy.py tests/unit/test_db_settings.py`
- note: add `local_day(ts, city)` with IANA zones (America/Bogota), UTC millisecond helpers, and a decimal-string money helper. Assert the asyncpg and psycopg arguments of section 5.5 (statement cache off, pool sizes, pre-ping, timeout) in a unit test that inspects the engine arguments without connecting. Add a repository-wide test that no migration declares `json`, `float`, or `real` columns.
- evidence: `pytest -q tests/unit/test_taxonomy.py tests/unit/test_db_settings.py` -> 6 passed, 1 skipped (db settings need sqlalchemy, unverified offline)

### T-010 Migration guardian and schema-list bootstrap
- status: todo
- spec: REQ-DAT-006, REQ-DAT-007, REQ-EXT-003
- owner: database-engineer
- depends: T-008
- write_scope: ops/check_migrations.py, ops/schemas.py, ops/bootstrap_roles.py, ops/bootstrap_roles.sql.example, tests/unit/test_migration_guard.py
- context: ops/check_migrations.py, ops/bootstrap_roles.sql.example, docs/adr/ADR-IMT-0001-ARCH.md sections 8.2, 17.3, E2.1
- size: M
- acceptance: `pytest -q tests/unit/test_migration_guard.py` and `python ops/check_migrations.py`
- note: one list (`ops/schemas.py`) drives the guardian and the role bootstrap. The guardian rejects DROP, RENAME, ALTER TYPE, and NOT NULL without default outside `contract_` files, and a `contract_` file in the same commit as application code (use a `--base` git ref option, tested with fixtures). The bootstrap enables pgvector, postgis, pg_cron, and pg_trgm idempotently and creates `_owner` and `_app` roles with no cross-schema grants. A test adds a fake schema to the list and expects both to accept it.
- evidence:

### T-011 Event catalog, envelope, and tolerant consumers
- status: todo
- spec: REQ-EVT-005, REQ-EXT-001
- owner: lead-backend-engineer
- depends: T-001
- write_scope: libs/contracts/events.py, libs/common/events_dispatch.py, tests/unit/test_contracts.py
- context: libs/contracts/events.py, libs/common/redis_streams.py, docs/adr/ADR-IMT-0001-ARCH.md sections 7.3, E1.4, E2.3
- size: M
- acceptance: `pytest -q tests/unit/test_contracts.py`
- note: define the L1 events and the common envelope (event_id, event_type, aggregate_type, aggregate_id, version, source_version, created_at, optional replay), list the reserved L2 and L3 event names without handlers, and add one dispatch helper that acknowledges and logs a single line for unknown types. Keep existing field names compatible. Payloads are never logged in full.
- evidence:

### T-012 Process supervisor, health, and readiness
- status: todo
- spec: REQ-EVT-006, REQ-RES-001
- owner: lead-backend-engineer
- depends: T-001
- write_scope: libs/common/supervise.py, tests/unit/test_supervise.py, tests/unit/test_health.py
- context: libs/common/supervise.py, services/*/procs.toml, services/*/app/main.py, docs/adr/ADR-IMT-0001-ARCH.md sections 6.3, 14.1
- size: M
- acceptance: `pytest -q tests/unit/test_supervise.py tests/unit/test_health.py`
- note: the supervisor must stop all children and exit 1 when one dies and must treat a missing heartbeat (90 s) as dead; implement what is missing. Health tests import every service app and check `/health` (always 200) and `/ready` (503 when its dependency fake fails). Do not edit service `main.py` files; report services that lack the endpoints so their lane fixes them.
- evidence:

### T-013 Datagen determinism and snapshot-shaped output
- status: todo
- spec: REQ-DAT-008
- owner: second-backend-engineer
- depends: T-001
- write_scope: datagen/, tests/unit/test_datagen.py
- context: datagen/builder.py, datagen/__main__.py, ops/seed/README.md
- size: M
- acceptance: `pytest -q tests/unit/test_datagen.py`
- note: two runs with the same seed give identical output (hash compare); the default dataset has 200 companies across Medellin, Bogota, and Cali, about 1,500 signals over 180 days, and 50 leads with outcomes. Add `--as-snapshots` that emits provider-shaped records for maps and computrabajo (for the seed and the offline pipeline test). Only company data; no personal data.
- evidence:

### T-014 Freeze shared contracts
- status: todo
- spec: REQ-EVT-005, REQ-EXT-001, REQ-EXT-004
- owner: architecture-reviewer
- depends: T-011, T-015, T-018
- write_scope: tests/unit/test_contract_snapshot.py, tests/unit/snapshots/, docs/contracts-freeze.md
- context: libs/contracts/, libs/common/settings.py, migrations/
- size: S
- acceptance: `pytest -q tests/unit/test_contract_snapshot.py`
- note: snapshot the JSON schemas of every model in `libs/contracts`, the event envelope, the settings field names, and the schema and table catalog. After this task, edits to existing files in `libs/contracts/`, `libs/common/settings.py`, and `migrations/` need the coordinator. Service tasks may add new modules under `libs/contracts/` and extend the snapshot for their own module only. Multiagent lanes start after this task.
- evidence:

### T-015 Settings and environment surface for run 1
- status: done
- spec: REQ-RUN-004, REQ-API-003, REQ-SEC-004
- owner: coordinator
- depends: T-001
- write_scope: libs/common/settings.py, .env.example, docker-compose.yml, tests/unit/test_settings.py
- context: libs/common/settings.py, .env.example, ops/check_configuration.py, SPEC.md (REQ-RUN-004, REQ-API-003)
- size: M
- acceptance: `pytest -q tests/unit/test_settings.py` and `python ops/check_configuration.py`
- note: declare every new variable once, with defaults safe for local use and no secret values: `INGEST_MODE` (mock|live), `INGEST_POLL_ENABLED`, `INGEST_POLL_INTERVAL_SECONDS`, `SOURCE_<NAME>_ENABLED` and `SOURCE_<NAME>_DATASET_ID` for maps, computrabajo, linkedin_companies, linkedin_jobs, instagram, rues, `BRIGHT_DATA_API_URL`, `BRIGHT_DATA_API_TOKEN`, `BRIGHT_DATA_WEBHOOK_SECRET`, `PUBLIC_WEBHOOK_BASE_URL`, `HOT_LEAD_WEBHOOK_URL`, `SOCIO_DATA_DIR`, `APP_PROFILE` (local|production), `FRONTEND_ORIGIN`, `DEV_USER_PASSWORD_<ROLE>`. Live mode with a missing credential raises a settings error naming the variable only. In compose, only add `env_file`/passthrough for these names; the rest of compose is T-073.
- evidence: `python ops/check_configuration.py` -> OK (82 variables); `pytest -q tests/unit/test_settings.py` -> skipped offline (pydantic_settings missing), unverified until CI

### T-016 Agent ownership registry
- status: todo
- spec: REQ-ARC-002
- owner: architecture-reviewer
- depends: T-001
- write_scope: .codex/agent-registry.yaml, .codex/agents/, tests/unit/test_registry.py
- context: AGENTS.md (Agent roster), .codex/agent-registry.yaml, SPEC.md section 3.1
- size: S
- acceptance: `pytest -q tests/unit/test_registry.py`
- note: add owners for identity, brief, pulse, streamlit, and mocks as in SPEC section 3.1, remove `analytics`, and keep the registry, the roster in `AGENTS.md`, and `.codex/agents/` in sync. The test checks every directory under `services/` has an owner.
- evidence:

### T-017 Guards: workflows, secrets, and static wiring
- status: todo
- spec: REQ-ORC-001, REQ-SEC-004
- owner: devops-engineer
- depends: T-002
- write_scope: ops/check_configuration.py, ops/check_workflows.py, Makefile, tests/unit/test_guards.py
- context: ops/check_configuration.py, Makefile, orchestration/n8n/
- size: M
- acceptance: `pytest -q tests/unit/test_guards.py` and `make static`
- note: add a workflow checker (no code nodes, no secret-like values, no business logic nodes) and wire `ops/check_traceability.py` and the workflow checker into `make static`. Add a secret-pattern scan over tracked text files that reports file names and counts only, never matched text.
- evidence:

### T-018 Schema additions for run 1 (expand migration)
- status: todo
- spec: REQ-COR-001, REQ-DAT-005, REQ-EXT-004
- owner: database-engineer
- depends: T-008, T-010
- write_scope: migrations/, tests/unit/test_schema_catalog.py
- context: SPEC.md section 3.2, migrations/, docs/adr/ADR-IMT-0001-ARCH.md sections 5.1, 5.4, E2.1
- size: L
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_schema_catalog.py`
- note: bring the migrations in line with SPEC section 3.2 in one new expand migration: every listed table exists in its owning schema; `analytics` objects live in `pulse`; brief tables live in `brief`; each consumer schema has `processed_event`; add `ingest.pending_snapshot`, `ingest.failed_record`, `core.job`, `core.upload_blob`, `core.tenant_config` (with ETag version) plus a history table, `core.outbox_dead_letter`, `core.audit_log`, `core.lead_activity` (channel, source), and the lead columns `source`, `city`, `service_line`, and nullable `holdout`; enable RLS and tenant policies on company, lead, and project. The test parses the SQL against the catalog.
- evidence:

### T-020 Company upsert API with history
- status: todo
- spec: REQ-COR-007
- owner: lead-backend-engineer
- depends: T-014, T-018
- write_scope: services/core/, libs/contracts/companies.py, tests/unit/test_company_upsert.py
- context: services/core/app/main.py, services/core/app/repository.py, libs/contracts/events.py, docs/adr/ADR-IMT-0001-ARCH.md section 5.3
- size: M
- acceptance: `pytest -q tests/unit/test_company_upsert.py`
- note: implement `POST /internal/core/companies/upsert`, SCD2 history, version bump, and the event in the same transaction. Test with a fake repository that records the transaction boundary; also cover repeating the same payload.
- evidence:

### T-021 Lead model, state machine, and list filters
- status: todo
- spec: REQ-COR-002
- owner: lead-backend-engineer
- depends: T-020
- write_scope: services/core/, libs/domain/pipeline.py, libs/contracts/leads.py, tests/unit/test_lead_state.py
- context: libs/domain/pipeline.py, services/core/app/main.py, services/core/app/repository.py, docs/adr/ADR-IMT-0001-ARCH.md section E1.1
- size: L
- acceptance: `pytest -q tests/unit/test_lead_state.py`
- note: valid transitions per the E1.1 diagram, `expected_version` with 409 and the current state, `Idempotency-Key` on create, filters (state, service_line, city, segment, min_score) with keyset cursor, `GET /leads/{id}`, and the kanban read model. Pure transition logic lives in `libs/domain/pipeline.py` with at least 90 percent coverage.
- evidence:

### T-022 Outcomes, activities, project conversion, and projects read
- status: todo
- spec: REQ-COR-003, REQ-COR-005
- owner: lead-backend-engineer
- depends: T-021
- write_scope: services/core/, libs/contracts/projects.py, tests/unit/test_outcomes.py, tests/unit/test_lead_actions.py
- context: services/core/app/main.py, services/core/app/repository.py, docs/adr/ADR-IMT-0001-ARCH.md sections 3 (P7), E1.1
- size: L
- acceptance: `pytest -q tests/unit/test_outcomes.py tests/unit/test_lead_actions.py`
- note: outcome capture (contact, response, meeting, close, spam) emitting `lead.outcome_recorded`; activity logging; lead to project conversion in one transaction; replace the 501 on `GET /internal/core/projects` with a real read model and add `POST /internal/core/projects`.
- evidence:

### T-023 Tenant configuration with ETag and reserved keys
- status: todo
- spec: REQ-COR-004, REQ-EXT-002
- owner: lead-backend-engineer
- depends: T-021
- write_scope: services/core/, libs/contracts/tenant_config.py, tests/unit/test_tenant_config.py
- context: services/core/app/main.py, docs/adr/ADR-IMT-0001-ARCH.md sections E1.1, E1.6, E2.3
- size: M
- acceptance: `pytest -q tests/unit/test_tenant_config.py`
- note: Pydantic model for `brief_rules` and the reserved `funnel_rules` with `geo_overrides`; `GET` and `PUT /internal/core/admin/tenant-config` with `If-Match` (412 on stale); previous version stored in the history table; emits `tenant.config_updated`. Unknown top-level keys are rejected; reserved keys are accepted without a migration.
- evidence:

### T-024 Automatic qualification consumer
- status: todo
- spec: REQ-COR-008
- owner: lead-backend-engineer
- depends: T-023
- write_scope: services/core/, tests/unit/test_auto_qualification.py
- context: services/core/app/worker.py, libs/domain/pipeline.py, docs/adr/ADR-IMT-0001-ARCH.md sections E1.1, E1.6
- size: M
- acceptance: `pytest -q tests/unit/test_auto_qualification.py`
- note: consume `lead.score_updated` with the inbox pattern; create or advance leads by the tenant thresholds; never move backward or override a human-set status; write `audit_log`; emit `lead.created` for new leads.
- evidence:

### T-025 Dead letter queue and republish
- status: todo
- spec: REQ-EVT-003, REQ-EVT-004
- owner: lead-backend-engineer
- depends: T-020
- write_scope: services/core/, tests/unit/test_dead_letter.py, tests/unit/test_republish.py
- context: services/core/app/outbox_relay.py, libs/common/retry.py, docs/adr/ADR-IMT-0001-ARCH.md sections 7.5, E1.4
- size: M
- acceptance: `pytest -q tests/unit/test_dead_letter.py tests/unit/test_republish.py`
- note: backoff 10 s, 30 s, 90 s, 5 min, 15 min then `core.outbox_dead_letter` with an alert log line; `python -m services.core.app.republish --target <pulse|brief|ml> --since <date>` replays with `replay: true`, requires `--confirm`, and blocks writes to the target while running (lock key).
- evidence:

### T-026 CSV imports as background jobs
- status: todo
- spec: REQ-ING-005
- owner: lead-backend-engineer
- depends: T-020
- write_scope: services/core/, libs/contracts/imports.py, tests/unit/test_imports.py
- context: services/core/app/main.py, services/core/app/worker.py, docs/adr/ADR-IMT-0001-ARCH.md section E1.7
- size: L
- acceptance: `pytest -q tests/unit/test_imports.py`
- note: implement the 202 plus job flow, limits (10 MB, 100,000 rows, 3 active jobs per tenant with 429), Polars chunked parsing with Pydantic validation, upsert by `row_sha256`, events per changed aggregate, and blob cleanup. Add `polars` to the `analytics` extra only if it is not already a base dependency; ask the coordinator for `pyproject.toml` changes.
- evidence:

### T-027 Retention purge and opt-out
- status: todo
- spec: REQ-CMP-001
- owner: lead-backend-engineer
- depends: T-020
- write_scope: services/core/, tests/unit/test_retention.py, docs/compliance/processing-activities.md
- context: docs/adr/ADR-IMT-0001-ARCH.md section 13.4, services/core/app/repository.py
- size: M
- acceptance: `pytest -q tests/unit/test_retention.py`
- note: purge companies 12 months after their last signal (job callable from a scheduler, dry-run option), idempotent opt-out delete endpoint, a guard that rejects storing personal identifiers (document numbers, private phones), and a short processing-activities record in English.
- evidence:

### T-030 Identity service: Django project, RBAC, JWKS
- status: todo
- spec: REQ-IDN-001
- owner: lead-backend-engineer
- depends: T-014, T-018
- write_scope: services/identity/, pyproject.toml (identity extra only), tests/unit/test_identity_rbac.py
- context: services/identity/app/main.py, services/identity/README.md, docs/adr/ADR-IMT-0001-ARCH.md sections E1.1, E1.2
- size: L
- acceptance: `pytest -q tests/unit/test_identity_rbac.py`
- note: Django plus DRF in `services/identity`, tables in the `identity` schema, models for user, tenant, role (admin, strategist, intake, bd_rep, analyst), and scope (city, service line), an RS256 key pair with a JWKS endpoint, service tokens, and the Django admin only on the internal network. Tests use SQLite settings and the Django test client. Decision D-14: if Django proves impractical in the sandbox after three focused attempts, mark `blocked`.
- evidence:

### T-031 Identity sessions, CSRF, local profile, and dev users
- status: todo
- spec: REQ-IDN-002
- owner: lead-backend-engineer
- depends: T-030, T-015
- write_scope: services/identity/, tests/unit/test_identity_session.py
- context: services/identity/, libs/common/security.py, docs/adr/ADR-IMT-0001-ARCH.md sections 13.1, E1.1
- size: M
- acceptance: `pytest -q tests/unit/test_identity_session.py`
- note: login sets the cookie attributes of REQ-IDN-002 in the production profile; the local profile drops the `__Host-` prefix and `Secure`; sliding renewal, 30 minutes of inactivity, 12 hours absolute, logout invalidation. A management command creates one development user per role with passwords taken from the environment (never hard-coded).
- evidence:

### T-032 Gateway: JWT/JWKS, route table, tenant propagation, local profile
- status: todo
- spec: REQ-GW-003, REQ-IDN-003, REQ-API-002, REQ-API-003
- owner: lead-backend-engineer
- depends: T-031, T-020, T-021
- write_scope: services/gateway/, tests/unit/test_gateway_auth.py, tests/unit/test_gateway_routes.py, tests/unit/test_tenant_isolation.py, tests/unit/test_profiles.py
- context: services/gateway/app/main.py, libs/common/security.py, docs/adr/ADR-IMT-0001-ARCH.md sections 6.4, E1.8, E1.10, 12.4
- size: L
- acceptance: `pytest -q tests/unit/test_gateway_auth.py tests/unit/test_gateway_routes.py tests/unit/test_tenant_isolation.py tests/unit/test_profiles.py`
- note: verify JWTs against the identity JWKS (cached 10 minutes), propagate tenant and role headers, enforce tenant isolation (404 across tenants), implement the route table of REQ-API-002 (pulse instead of analytics; 404 `not_in_layer` for L2 and L3 resources), add the CORS allowlist from `FRONTEND_ORIGIN` for the local profile, apply per-route upstream timeouts, and return 503 only for routes whose upstream `/ready` is red (poll every 10 s).
- evidence:

### T-033 Brief cache, single-flight, and rule-based fallback
- status: todo
- spec: REQ-BRF-001, REQ-BRF-002
- owner: lead-backend-engineer
- depends: T-014, T-018
- write_scope: services/brief/, tests/unit/test_brief_cache.py, tests/unit/test_brief_singleflight.py
- context: services/brief/app/main.py, services/brief/app/single_flight.py, services/brief/app/rule_based.py, docs/adr/ADR-IMT-0001-ARCH.md sections 10.2, E1.6
- size: L
- acceptance: `pytest -q tests/unit/test_brief_cache.py tests/unit/test_brief_singleflight.py`
- note: implement the three states (hit, stale, degraded) with the `X-Brief-Cache` header, first-read blocking generation, stale-while-revalidate, the Redis lock (SET NX PX 30000, Lua release), the 5-second summarizer timeout with rule fallback, and 503 `db_unavailable` when core is unreachable. The summarizer is a replaceable interface; ship the rule-based one (decision D-09).
- evidence:

### T-034 Brief alert rules per tenant
- status: todo
- spec: REQ-BRF-003
- owner: lead-backend-engineer
- depends: T-033, T-023
- write_scope: services/brief/, tests/unit/test_brief_rules.py
- context: services/brief/app/rule_based.py, libs/contracts/tenant_config.py, docs/adr/ADR-IMT-0001-ARCH.md section E1.1
- size: M
- acceptance: `pytest -q tests/unit/test_brief_rules.py`
- note: alert codes and severities from E1.1, thresholds and weights from `brief_rules`, disabled codes, and lazy invalidation on `tenant.config_updated`.
- evidence:

### T-040 Raw snapshot ingestion and change detection
- status: todo
- spec: REQ-ING-001
- owner: second-backend-engineer
- depends: T-014, T-018
- write_scope: services/ingest/, tests/unit/test_ingest_snapshot.py
- context: services/ingest/app/main.py, services/ingest/app/repository.py, docs/adr/ADR-IMT-0001-ARCH.md sections 4.1, 4.3
- size: M
- acceptance: `pytest -q tests/unit/test_ingest_snapshot.py`
- note: store snapshots with sha256 and `UNIQUE(source, external_id, sha256)`, create new signals only for changed content, enqueue one processor job per snapshot, and keep the webhook response fast (202).
- evidence:

### T-041 Budget caps and failed fetch handling
- status: todo
- spec: REQ-ING-002
- owner: second-backend-engineer
- depends: T-040
- write_scope: services/ingest/, tests/unit/test_ingest_budget.py
- context: services/ingest/app/repository.py, docs/adr/ADR-IMT-0001-ARCH.md section 4.3
- size: M
- acceptance: `pytest -q tests/unit/test_ingest_budget.py`
- note: monthly cap per source in `ingest.budget` checked before any trigger; failures stored in `ingest.failed_fetch` with exponential backoff and dead letter after 5 failures.
- evidence:

### T-042 Connector interface, mapping files, and Bright Data mock
- status: todo
- spec: REQ-ING-004, REQ-ING-007
- owner: second-backend-engineer
- depends: T-040, T-015
- write_scope: services/ingest/, services/mocks/, libs/domain/source_mappings/, tests/fixtures/, tests/unit/test_connectors.py, tests/unit/test_mocks.py
- context: services/ingest/app/, services/mocks/app/main.py, docs/adr/ADR-IMT-0001-ARCH.md sections 4.1, 4.4
- size: L
- acceptance: `pytest -q tests/unit/test_connectors.py tests/unit/test_mocks.py`
- note: define `SourceConnector` (trigger, fetch, normalize), the YAML mapping format, mapping files for maps and computrabajo, and the mock connector. Extend the Flask mock to emulate trigger, progress, snapshot download, latency, failure rate, and signed webhooks. If `tests/fixtures/brightdata/` contains real samples (decision D-13), build mappings from them; otherwise mark each mapping `unverified: true` and list it for the run report.
- evidence:

### T-043 Bright Data live connector and polling fallback
- status: todo
- spec: REQ-ING-004, REQ-ING-008, REQ-RUN-004
- owner: second-backend-engineer
- depends: T-042, T-041
- write_scope: services/ingest/, tests/unit/test_connectors.py, tests/unit/test_ingest_poll.py, tests/unit/test_source_config.py
- context: services/ingest/app/, libs/common/settings.py (read only), docs/adr/ADR-IMT-0001-ARCH.md sections 4.1, 4.3
- size: L
- acceptance: `pytest -q tests/unit/test_connectors.py tests/unit/test_ingest_poll.py tests/unit/test_source_config.py`
- note: implement the Bright Data Datasets API client with `httpx` (Bearer token, trigger with the notify URL built from `PUBLIC_WEBHOOK_BASE_URL`, progress, snapshot download, retry with backoff only for idempotent calls). Track triggers in `ingest.pending_snapshot` and poll when `INGEST_POLL_ENABLED`; deduplicate against webhook delivery. `INGEST_MODE` selects mock or live per source; a live source without credentials fails at startup naming the variable. Tests use `httpx.MockTransport` only.
- evidence:

### T-044 Entity resolution against RUES (mock lookup)
- status: todo
- spec: REQ-ING-003
- owner: second-backend-engineer
- depends: T-040
- write_scope: libs/domain/entity_resolution.py, services/ingest/, tests/unit/test_entity_resolution.py
- context: libs/domain/entity_resolution.py, services/ingest/app/repository.py, docs/adr/ADR-IMT-0001-ARCH.md section 5.3
- size: L
- acceptance: `pytest -q tests/unit/test_entity_resolution.py`
- note: thresholds 0.9 and 0.6, `entity_match_log` for review, `rues_lookup_queue`, quarantine after two failed attempts, and a `RuesLookup` interface with a mock. Splink is optional: use a pure-Python scorer (rapidfuzz or difflib) behind the same interface and record that choice in the run report.
- evidence:

### T-045 Processor to core flow
- status: todo
- spec: REQ-ING-009
- owner: second-backend-engineer
- depends: T-044, T-042, T-020
- write_scope: services/ingest/, tests/unit/test_ingest_to_core.py
- context: services/ingest/app/processor.py, services/ingest/app/repository.py, libs/contracts/companies.py
- size: L
- acceptance: `pytest -q tests/unit/test_ingest_to_core.py`
- note: after resolution call core's upsert and signal endpoints through an injectable client with the service token; apply the mapping files; write failures to `ingest.failed_record`; never block the batch.
- evidence:

### T-046 Enrichment agent contract
- status: todo
- spec: REQ-ING-006
- owner: second-backend-engineer
- depends: T-045
- write_scope: services/ingest/, tests/unit/test_enrichment.py
- context: services/ingest/app/, docs/adr/ADR-IMT-0001-ARCH.md sections 4.2, E1.1
- size: M
- acceptance: `pytest -q tests/unit/test_enrichment.py`
- note: replaceable `EnrichmentAgent` interface (mock in run 1), rate limit and daily budget, `pending_retry` with backoff on provider failure, and a Brief warning code rather than a 500.
- evidence:

### T-047 n8n source workflows
- status: todo
- spec: REQ-ORC-001
- owner: second-backend-engineer
- depends: T-017, T-042
- write_scope: orchestration/n8n/, tests/unit/test_workflows.py
- context: orchestration/n8n/, ops/check_workflows.py, docs/adr/ADR-IMT-0001-ARCH.md section 4.1
- size: M
- acceptance: `pytest -q tests/unit/test_workflows.py` and `python ops/check_workflows.py`
- note: versioned workflows for maps and computrabajo (trigger, notify URL, budget check, notification) that call ingest only; no code nodes, no secrets; credentials referenced by n8n credential names.
- evidence:

### T-050 Merge analytics into pulse
- status: todo
- spec: REQ-ARC-001
- owner: second-backend-engineer
- depends: T-014, T-018
- write_scope: services/pulse/, services/analytics/, tests/unit/test_pulse_consumer.py
- context: services/analytics/app/, services/pulse/app/, docs/adr/ADR-IMT-0001-ARCH.md section E2.2
- size: L
- acceptance: `pytest -q tests/unit/test_pulse_consumer.py` and `python -c "import pathlib,sys; sys.exit(1 if pathlib.Path('services/analytics').exists() else 0)"`
- note: port the analytics consumer, repository, and market and segment endpoints into `services/pulse` using the `pulse` schema and the consumer-owned `processed_event`, then delete `services/analytics`. Do not edit compose or the gateway route table (T-073 and T-032 own them).
- evidence:

### T-051 Pulse endpoints, freshness, and response rules
- status: todo
- spec: REQ-PUL-001, REQ-EXT-004
- owner: second-backend-engineer
- depends: T-050
- write_scope: services/pulse/, libs/contracts/pulse.py, tests/unit/test_pulse_contract.py
- context: services/pulse/app/main.py, services/pulse/app/models.py, docs/adr/ADR-IMT-0001-ARCH.md section E1.5
- size: M
- acceptance: `pytest -q tests/unit/test_pulse_contract.py`
- note: the five Pulse endpoints plus `ops/health` with `as_of`, `stale` after 26 hours, `low_sample`, null for no data, optional `method_version`, and `Cache-Control: private, max-age=60`.
- evidence:

### T-052 Pulse incremental rollups
- status: todo
- spec: REQ-PUL-002
- owner: second-backend-engineer
- depends: T-050
- write_scope: services/pulse/, tests/unit/test_rollups.py
- context: services/pulse/app/repository.py, libs/common/time.py, docs/adr/ADR-IMT-0001-ARCH.md sections 8.1, E1.3
- size: M
- acceptance: `pytest -q tests/unit/test_rollups.py`
- note: event-driven update of only the affected local day, `local_day(ts, city)` for grouping, and a daily close job.
- evidence:

### T-053 ML scoring service with single-flight and rule score
- status: todo
- spec: REQ-ML-001, REQ-COR-006
- owner: second-backend-engineer
- depends: T-014, T-018
- write_scope: services/ml/, libs/domain/scoring.py, tests/unit/test_scoring.py, tests/unit/test_ml_singleflight.py
- context: services/ml/app/, libs/domain/scoring.py, docs/adr/ADR-IMT-0001-ARCH.md sections 10.1 to 10.3
- size: L
- acceptance: `pytest -q tests/unit/test_scoring.py tests/unit/test_ml_singleflight.py`
- note: deterministic weighted rule (ICP fit, signal intensity, territory placeholder) with weights from `core.lead_score_rule` and tenant config, `source_version` and `stale` handling, the Redis lock, and `lead.score_updated` publication. Pure scoring logic lives in `libs/domain/scoring.py`.
- evidence:

### T-054 ML segmentation pipeline
- status: todo
- spec: REQ-ML-002
- owner: second-backend-engineer
- depends: T-053, T-013
- write_scope: services/ml/, tests/unit/test_segmentation.py
- context: services/ml/app/, datagen/, pyproject.toml (ml extra)
- size: M
- acceptance: `pytest -q tests/unit/test_segmentation.py`
- note: nightly batch on datagen data: firmographic features, UMAP plus HDBSCAN with K-Prototypes comparison, `ml.company_segment`, and a versioned `ml.model_version`. Use fixed random seeds. Skip heavy imports at module load so the service starts without the `ml` extra.
- evidence:

### T-055 dbt star model and Parquet materialization
- status: todo
- spec: REQ-PUL-003
- owner: second-backend-engineer
- depends: T-050
- write_scope: transform/
- context: transform/, docs/adr/ADR-IMT-0001-ARCH.md sections 5.2, 8.1
- size: M
- acceptance: `dbt parse --project-dir transform --profiles-dir transform` (profiles from `profiles.yml.example`)
- note: dimensions and facts listed in REQ-PUL-003, schema tests, MetricFlow YAML for the core measures, and a Parquet export model or script. No database is available in the sandbox: parse and compile only.
- evidence:

### T-056 Streamlit foundation and container
- status: todo
- spec: REQ-STR-001, REQ-STR-002
- owner: second-backend-engineer
- depends: T-051
- write_scope: services/streamlit/, pyproject.toml (streamlit extra), tests/unit/test_streamlit_helpers.py
- context: services/streamlit/, docs/adr/ADR-IMT-0001-ARCH.md sections 9.1, 9.3, 9.4
- size: M
- acceptance: `pytest -q tests/unit/test_streamlit_helpers.py`
- note: real Streamlit app with `st.navigation`, `safe_fetch` returning (data, error) with `as_of`, stale warning, per-card error and retry, role gating, session-state filters with `st.query_params`, and export visibility rules. No database credentials; reads Parquet and the gateway API. The replacement of the temporary uvicorn shell in compose is T-073.
- evidence:

### T-057 Streamlit pages 1 to 4
- status: todo
- spec: REQ-STR-001
- owner: second-backend-engineer
- depends: T-056
- write_scope: services/streamlit/pages/, tests/unit/test_streamlit_pages_a.py
- context: services/streamlit/, docs/adr/ADR-IMT-0001-ARCH.md section 9.2
- size: M
- acceptance: `pytest -q tests/unit/test_streamlit_pages_a.py` (Streamlit AppTest)
- note: Market, Leads, Segmentos, and Territorios pages. Territorios shows only what L1 provides and an explicit placeholder for the IOZ (L3).
- evidence:

### T-058 Streamlit pages 5 to 7
- status: todo
- spec: REQ-STR-001
- owner: second-backend-engineer
- depends: T-056, T-022
- write_scope: services/streamlit/pages/, tests/unit/test_streamlit_pages_b.py
- context: services/streamlit/, docs/adr/ADR-IMT-0001-ARCH.md section 9.2
- size: M
- acceptance: `pytest -q tests/unit/test_streamlit_pages_b.py` (Streamlit AppTest)
- note: Pipeline (kanban from the gateway), Proyectos, and Ops (freshness per source, outbox backlog, service health).
- evidence:

### T-035 Hot lead alert notifier
- status: todo
- spec: REQ-ALR-001
- owner: lead-backend-engineer
- depends: T-024
- write_scope: services/core/, tests/unit/test_hot_alert.py
- context: services/core/app/worker.py, orchestration/n8n/wf_hot_lead_alert.json, docs/adr/ADR-IMT-0001-ARCH.md sections 1.3, 4.1
- size: M
- acceptance: `pytest -q tests/unit/test_hot_alert.py`
- note: when auto-qualification or a user moves a lead to Hot, post one alert to `HOT_LEAD_WEBHOOK_URL` (mock Telegram locally), once per lead and version, with backoff on failure and no effect on the commit. Test the 60-second bound with a fake clock.
- evidence:

### T-048 LinkedIn and Instagram sources
- status: todo
- spec: REQ-ING-004
- owner: second-backend-engineer
- depends: T-043
- write_scope: libs/domain/source_mappings/, services/ingest/, tests/fixtures/, tests/unit/test_connectors_more.py
- context: libs/domain/source_mappings/, services/ingest/app/, docs/adr/ADR-IMT-0001-ARCH.md section 4.4
- size: M
- acceptance: `pytest -q tests/unit/test_connectors_more.py`
- note: add mappings and connector configuration for linkedin_companies, linkedin_jobs, and instagram. Mark each mapping `unverified: true` unless real samples exist under `tests/fixtures/brightdata/`.
- evidence:

### T-049 Remaining n8n workflows
- status: todo
- spec: REQ-ORC-001
- owner: second-backend-engineer
- depends: T-047, T-035
- write_scope: orchestration/n8n/, tests/unit/test_workflows.py
- context: orchestration/n8n/, ops/check_workflows.py, docs/adr/ADR-IMT-0001-ARCH.md section 4.1
- size: M
- acceptance: `pytest -q tests/unit/test_workflows.py` and `python ops/check_workflows.py`
- note: workflows for linkedin companies backfill, linkedin jobs, instagram, RUES delta, socioeconomic refresh, hot lead alert (webhook trigger to Telegram), and ops health. Orchestration only, no secrets, credentials by n8n credential name.
- evidence:

### T-059 Socioeconomic indicators and opportunity index
- status: todo
- spec: REQ-SOC-001
- owner: second-backend-engineer
- depends: T-050, T-018
- write_scope: services/pulse/, libs/domain/socio.py, tests/fixtures/socio/, tests/unit/test_socio_indices.py
- context: services/pulse/app/, docs/adr/ADR-IMT-0001-ARCH.md section 11
- size: L
- acceptance: `pytest -q tests/unit/test_socio_indices.py`
- note: loader for indicator files from `SOCIO_DATA_DIR`, z-scores within city, IPET, IMD, and IOP with PCA or entropy weights and `method_version`, the `/territory/opportunity` read endpoint, and a fixture dataset. Pure index math lives in `libs/domain/socio.py`. Spatial autocorrelation (Moran's I) is L3 and out of scope here.
- evidence:

### T-060 Zero trust between services
- status: todo
- spec: REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-032, T-050
- write_scope: libs/common/internal_auth.py, services/*/app/main.py (auth dependency only), tests/unit/test_internal_auth.py
- context: libs/common/internal_auth.py, libs/common/security.py, docs/adr/ADR-IMT-0001-ARCH.md section 6.4
- size: M
- acceptance: `pytest -q tests/unit/test_internal_auth.py` and `python ops/check_configuration.py`
- note: signed service tokens with expiry for service-to-service calls, `_app` role per service in settings, Redis ACL users `edge` and `data` documented in compose configuration checks, and `/internal/*` never routed by the gateway (test).
- evidence:

### T-061 Failure matrix tests
- status: todo
- spec: REQ-RES-002
- owner: qa-reviewer
- depends: T-033, T-051, T-053, T-032
- write_scope: tests/unit/test_failure_matrix.py
- context: services/gateway/app/main.py, services/brief/app/main.py, services/pulse/app/main.py, docs/adr/ADR-IMT-0001-ARCH.md section E1.10
- size: M
- acceptance: `pytest -q tests/unit/test_failure_matrix.py`
- note: fault injection with fakes for ml, pulse, brief, and Redis as described in REQ-RES-002. Report production defects instead of changing production code.
- evidence:

### T-062 Graceful shutdown, structured logs, and metrics
- status: todo
- spec: REQ-EVT-007, REQ-OBS-001
- owner: lead-backend-engineer
- depends: T-060, T-061
- write_scope: libs/common/logging.py, libs/common/lifecycle.py, services/*/app/main.py, tests/unit/test_lifespan.py, tests/unit/test_logging.py
- context: libs/common/logging.py, libs/common/request_id.py, docs/adr/ADR-IMT-0001-ARCH.md sections 14.3, 19
- size: M
- acceptance: `pytest -q tests/unit/test_lifespan.py tests/unit/test_logging.py`
- note: one shared lifespan helper (stop accepting, wait 25 s, flush the relay, ack the batch, close pools), one-line JSON logs with `request_id`, and Prometheus metrics at `/internal/metrics` for every service.
- evidence:

### T-063 Verified encrypted backups
- status: todo
- spec: REQ-BAK-001
- owner: devops-engineer
- depends: T-018
- write_scope: ops/backups/, .github/workflows/backups.yml, tests/unit/test_backups.py
- context: ops/backups/, .github/workflows/backups.yml, docs/adr/ADR-IMT-0001-ARCH.md section 18
- size: M
- acceptance: `pytest -q tests/unit/test_backups.py`
- note: per-schema dump with the owner role, sha256, restore into an ephemeral database and check migration versions and table counts, `age` encryption, retention 7 and 28 days, and RPO and RTO in `ops/backups/README.md`. Test the orchestration logic with fakes for pg_dump and age.
- evidence:

### T-064 Eleven-stage CI pipeline
- status: todo
- spec: REQ-CI-002
- owner: devops-engineer
- depends: T-007, T-063, T-070
- write_scope: `.github/workflows/ci.yml`, tests/unit/test_ci_workflow.py
- context: `.github/workflows/ci.yml`, docs/adr/ADR-IMT-0001-ARCH.md section E1.9
- size: M
- acceptance: `pytest -q tests/unit/test_ci_workflow.py`
- note: stages static, unit, integration, contracts (oasdiff against the committed `docs/api/openapi.json`), e2e, images, deploy migrate, deploy backend, deploy frontend, deploy streamlit, deploy smoke, in order. Deploy stages are skipped without environment secrets. No secret values in the file.
- evidence:

### T-065 Coverage gate
- status: todo
- spec: REQ-QA-003
- owner: qa-reviewer
- depends: T-077
- write_scope: pyproject.toml (coverage settings), Makefile (`coverage` target), tests/unit/
- context: pyproject.toml, Makefile
- size: S
- acceptance: `make coverage` reports at least 80 percent for `libs` and `services` and at least 90 percent for `libs/domain`
- note: add missing unit tests for any module below the threshold in `libs/domain`; list modules below 80 percent elsewhere in the run report with the reason.
- evidence:

### T-070 OpenAPI export
- status: todo
- spec: REQ-API-001
- owner: lead-backend-engineer
- depends: T-032, T-051, T-022, T-026
- write_scope: services/gateway/, ops/export_openapi.py, docs/api/openapi.json, Makefile (`openapi` target only), tests/unit/test_openapi.py
- context: services/gateway/app/main.py, docs/adr/ADR-IMT-0001-ARCH.md section 12.6
- size: M
- acceptance: `pytest -q tests/unit/test_openapi.py` and `make openapi` leaves `git diff --exit-code docs/api/openapi.json` clean
- note: the gateway proxies by prefix, so build the document from an explicit route registry (path, method, request and response models, errors, headers) shared with the route table of T-032. The test fails for a route missing from the document or a stale committed file.
- evidence:

### T-071 Migration runner
- status: todo
- spec: REQ-RUN-002
- owner: database-engineer
- depends: T-018
- write_scope: ops/migrate.py, tests/unit/test_migrate.py
- context: ops/check_migrations.py, migrations/
- size: M
- acceptance: `pytest -q tests/unit/test_migrate.py`
- note: ordered application, one transaction per file, `public.schema_migration` with checksum, refusal on changed checksums, idempotent re-run, and a `--dry-run` listing. Test with a fake connection.
- evidence:

### T-072 Demo seed
- status: todo
- spec: REQ-RUN-003
- owner: second-backend-engineer
- depends: T-045, T-053, T-052, T-013
- write_scope: ops/seed/, tests/unit/test_seed.py
- context: datagen/, ops/seed/README.md, services/ingest/app/, services/core/app/
- size: M
- acceptance: `pytest -q tests/unit/test_seed.py`
- note: `python -m ops.seed load|clear` feeds the datagen snapshots through the ingest code path (mock mode) so every downstream table fills; idempotent; rows tagged `source=demo`; `clear` removes only those rows through service APIs.
- evidence:

### T-073 Compose, images, and health probes
- status: todo
- spec: REQ-DEP-001, REQ-ARC-001, REQ-STR-002
- owner: devops-engineer
- depends: T-050, T-056, T-071, T-015
- write_scope: docker-compose.yml, deploy/, tests/unit/test_compose.py
- context: docker-compose.yml, deploy/Dockerfile.python, deploy/Caddyfile.example, SPEC.md section 3.1
- size: L
- acceptance: `pytest -q tests/unit/test_compose.py`, plus `make compose-config` when the Docker CLI exists
- note: services exactly as SPEC section 3.1 (no analytics); one-shot `migrate` service with `service_completed_successfully`; workers for core relay and consumers, ingest processor and poller, pulse consumer, ml consumer, brief worker; a real Streamlit container with health `/_stcore/health`; healthchecks for every API; `env_file: .env`; services read only their own database role. The test parses the compose file and asserts topology, dependencies, and the absence of credentials in the gateway.
- evidence:

### T-074 The make invent-tracker command
- status: todo
- spec: REQ-RUN-001
- owner: devops-engineer
- depends: T-073, T-072, T-070
- write_scope: Makefile, ops/bootstrap_env.py, tests/unit/test_make_targets.py, tests/unit/test_bootstrap_env.py
- context: Makefile, .env.example, docker-compose.yml
- size: M
- acceptance: `make -n invent-tracker` and `pytest -q tests/unit/test_make_targets.py tests/unit/test_bootstrap_env.py`
- note: targets `invent-tracker`, `invent-tracker-down`, `invent-tracker-reset` (asks for confirmation), `seed`, `seed-clear`, `openapi`, and `test-integration`. `ops/bootstrap_env.py` copies `.env.example` to `.env` only when missing and fills local secrets (service token, edge secret, database and Redis passwords, dev user passwords) from `secrets` without printing them. After start it waits for the gateway `/ready` and prints URLs for the gateway, Streamlit, and n8n.
- evidence:

### T-075 Doctor command
- status: todo
- spec: REQ-RUN-005
- owner: devops-engineer
- depends: T-074, T-043
- write_scope: ops/doctor.py, Makefile (`doctor` target only), tests/unit/test_doctor.py
- context: ops/bootstrap_env.py, libs/common/settings.py
- size: M
- acceptance: `pytest -q tests/unit/test_doctor.py`
- note: variable-name-only report of set and missing settings, active sources and modes, service health through the gateway, and webhook reachability when configured. The test plants a sentinel secret and asserts it never appears.
- evidence:

### T-076 Live verification command
- status: todo
- spec: REQ-RUN-006
- owner: second-backend-engineer
- depends: T-043, T-075
- write_scope: ops/verify_live.py, Makefile (`verify-live` target only), tests/unit/test_verify_live.py
- context: services/ingest/app/, ops/doctor.py
- size: M
- acceptance: `pytest -q tests/unit/test_verify_live.py`
- note: bounded collection (5 records) for one source, per-hop pass or fail report (trigger, snapshot, normalize, resolve, signal, score, API read), refusal in mock mode, and clear next steps on failure. Tested with fakes.
- evidence:

### T-077 Offline end-to-end pipeline test
- status: todo
- spec: REQ-QA-004
- owner: qa-reviewer
- depends: T-045, T-053, T-052, T-033, T-024, T-032
- write_scope: tests/unit/test_pipeline_flow.py, tests/unit/support_pipeline.py
- context: services/ingest/app/, services/core/app/, services/ml/app/, services/pulse/app/, services/brief/app/, services/gateway/app/main.py
- size: L
- acceptance: `pytest -q tests/unit/test_pipeline_flow.py` and `make test`
- note: wire in-memory fake repositories and fake Redis Streams so one mock snapshot travels the whole path of REQ-QA-004 and the final `/api/v1/leads`, `/api/v1/leads/{id}/brief`, and `/api/v1/pulse/*` payloads are asserted through the gateway ASGI app. Report production defects you find; fix only test code.
- evidence:

### T-078 Integration test tier
- status: todo
- spec: REQ-QA-005
- owner: qa-reviewer
- depends: T-071, T-077
- write_scope: tests/integration/
- context: tests/integration/conftest.py, ops/migrate.py, tests/unit/test_pipeline_flow.py
- size: M
- acceptance: `pytest -q tests/integration --collect-only` (the tests run only with database and Redis URLs set, by the owner)
- note: tests for migrations on an empty database, role isolation between schemas, the outbox relay against real Redis Streams, and the REQ-QA-004 flow producing rows in every expected schema. Marked `integration`; skipped when no database URL is set.
- evidence:

### T-079 Go-live runbook
- status: todo
- spec: REQ-DOC-001
- owner: architecture-reviewer
- depends: T-076
- write_scope: docs/runbooks/go-live.md, tests/unit/test_runbook.py
- context: docs/README.md, .env.example, ops/doctor.py, ops/verify_live.py
- size: S
- acceptance: `pytest -q tests/unit/test_runbook.py`
- note: step-by-step English runbook: credentials, enabling sources, webhook tunnel or polling fallback, mapping adjustments (including `unverified` mappings), `make doctor`, `make verify-live`, how data reaches `/api/v1`, how to point the frontend at the gateway with the local profile, and a troubleshooting table.
- evidence:

### T-080 Run 1 review pack
- status: todo
- spec: REQ-QA-001, REQ-TRC-001
- owner: architecture-reviewer
- depends: T-064, T-065, T-077, T-078, T-079
- write_scope: docs/runs/
- context: SPEC.md (sections 3 and 6), docs/adr/ADR-IMT-0001-ARCH.md section E2.5, TASKS.md
- size: M
- acceptance: `make static`, `make test`, `make coverage`, `make openapi`, and `python ops/check_traceability.py` all pass, and `make compose-config` passes where the Docker CLI exists
- note: walk the E2.5 checklist and the L1 definition of done, list unverified field mappings, blocked tasks, assumptions, and risks, and write the final section of `docs/runs/RUN-<run-id>.md`. Do not modify code; open follow-up items instead.
- evidence:

### T-100 Metric pack library
- status: todo
- spec: REQ-LNS-008
- owner: second-backend-engineer
- depends: T-080
- write_scope: libs/domain/metrics/, tests/unit/test_metrics_pack.py
- context: docs/adr/ADR-IMT-0002-LENS.md section 2.3
- size: M
- acceptance: `pytest -q tests/unit/test_metrics_pack.py`
- note: pure functions for AUC, PR-AUC, Brier, reliability table, lift@K, precision@K by subpopulation, NDCG@K, MAP, MRR, sMAPE, MASE, pinball loss, interval coverage, and Qini. Validate against hand-computed examples.
- evidence:

### T-101 Lens contract and five-question checker
- status: todo
- spec: REQ-LNS-001, REQ-LNS-002
- owner: lead-backend-engineer
- depends: T-080
- write_scope: libs/contracts/lens.py, ops/check_five_questions.py, docs/features/_TEMPLATE.md, tests/unit/test_lens_contract.py
- context: docs/adr/ADR-IMT-0002-LENS.md sections 2, 2.9
- size: M
- acceptance: `pytest -q tests/unit/test_lens_contract.py` and `python ops/check_five_questions.py`
- note: a quality-metrics envelope per lens, and a checker that reads feature specs under `docs/features/` and fails when any of the five answers is empty. Wire the checker into `make static` through the coordinator.
- evidence:

### T-102 Descriptive aggregation (D1)
- status: todo
- spec: REQ-LNS-003
- owner: second-backend-engineer
- depends: T-100
- write_scope: services/pulse/, tests/unit/test_descriptive_metrics.py
- context: services/pulse/app/, docs/adr/ADR-IMT-0002-LENS.md section 2.1
- size: M
- acceptance: `pytest -q tests/unit/test_descriptive_metrics.py`
- note: percentiles, MAD, IQR, HHI, and Gini as response fields of the Pulse endpoints, plus the project descriptive metrics.
- evidence:

### T-103 Profiling and topics (D2, D3)
- status: todo
- spec: REQ-LNS-004
- owner: second-backend-engineer
- depends: T-100, T-054
- write_scope: services/ml/, libs/domain/topics.py, tests/unit/test_profiling.py, tests/unit/test_topics.py
- context: services/ml/app/, docs/adr/ADR-IMT-0002-LENS.md section 2.1
- size: L
- acceptance: `pytest -q tests/unit/test_profiling.py tests/unit/test_topics.py`
- note: K-Prototypes profiling, Leiden communities, centrality, regex and dictionary tagging, YAKE, TF-IDF, and monthly LDA, publishing results to `pulse` through its API. Heavy libraries load lazily.
- evidence:

### T-104 Attribution (Dx1)
- status: todo
- spec: REQ-LNS-005
- owner: second-backend-engineer
- depends: T-100
- write_scope: services/ml/, libs/domain/attribution.py, tests/unit/test_attribution.py
- context: docs/adr/ADR-IMT-0002-LENS.md section 2.2
- size: M
- acceptance: `pytest -q tests/unit/test_attribution.py`
- note: last-touch, first-touch, time-decay, and Markov removal-effect attribution; Shapley only behind a flag that requires 100 labeled closes.
- evidence:

### T-105 Causal inference (Dx2)
- status: todo
- spec: REQ-LNS-006
- owner: second-backend-engineer
- depends: T-100
- write_scope: services/ml/, libs/domain/causal.py, tests/unit/test_causal.py
- context: docs/adr/ADR-IMT-0002-LENS.md section 2.2
- size: L
- acceptance: `pytest -q tests/unit/test_causal.py`
- note: DID, synthetic control, and propensity matching returning ATE with a 95 percent interval, ATT and ATU, p-value, effect size, robustness checks, and E-value; mark results inconclusive when the sign flips. Test on simulated data with a known effect.
- evidence:

### T-106 Drift and explainability (Dx3, Dx4)
- status: todo
- spec: REQ-LNS-007
- owner: second-backend-engineer
- depends: T-100, T-053
- write_scope: services/ml/, libs/domain/drift.py, tests/unit/test_drift.py, tests/unit/test_explain.py
- context: services/ml/app/, docs/adr/ADR-IMT-0002-LENS.md section 2.2
- size: L
- acceptance: `pytest -q tests/unit/test_drift.py tests/unit/test_explain.py`
- note: CUSUM, EWMA, change points, and PSI with the 0.1 and 0.25 thresholds, a nightly worker that alerts through an n8n webhook, and SHAP drivers plus counterfactuals attached to the Brief on regeneration.
- evidence:

### T-107 Validation schema (expand migration)
- status: todo
- spec: REQ-LNS-010
- owner: database-engineer
- depends: T-080
- write_scope: migrations/, ops/schemas.py, tests/unit/test_schema_catalog.py
- context: SPEC.md section 3.2, ops/schemas.py
- size: S
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_schema_catalog.py`
- note: create the `validation` schema and its six tables in an expand migration, operated by the `core` roles, each table with `source_version`, `project_id`, and `measured_at`; add the schema to the single schema list.
- evidence:

### T-108 Validation metrics V1 to V6 and thresholds
- status: todo
- spec: REQ-LNS-010, REQ-LNS-011
- owner: lead-backend-engineer
- depends: T-107, T-100
- write_scope: services/core/, libs/domain/validation.py, libs/domain/validation_thresholds.yaml, tests/unit/test_validation_metrics.py, tests/unit/test_validation_thresholds.py
- context: services/core/app/, docs/adr/ADR-IMT-0002-LENS.md section 2.5
- size: L
- acceptance: `pytest -q tests/unit/test_validation_metrics.py tests/unit/test_validation_thresholds.py`
- note: workers and pure functions for NPV, IRR, payback, DPV, EMV, CAC, LTV, LTV:CAC, CAC payback, gross margin, conversion, win rate, cycle time, velocity, coverage, MRR, NRR, GRR, Rule of 40, burn multiple, magic number, horizon mix, HHI, Sharpe-like ratio, and the V6 metrics. Thresholds live in YAML, not in code.
- evidence:

### T-109 Deterministic funnel engine
- status: todo
- spec: REQ-LNS-009
- owner: lead-backend-engineer
- depends: T-108, T-023
- write_scope: services/core/, libs/domain/funnel.py, config/funnel/, tests/unit/test_funnel.py
- context: services/core/app/, docs/adr/ADR-IMT-0002-LENS.md section 2.4
- size: L
- acceptance: `pytest -q tests/unit/test_funnel.py`
- note: gates G1 to G7 from versioned YAML checklists, verdicts ADVANCE, PIVOT, KILL, and WAIT, audit rows for each decision, and the `funnel.*` events through the outbox. Ship example checklists marked `example: true`; production must refuse them unless `FUNNEL_ALLOW_EXAMPLE_CHECKLISTS=true` (decision D-06).
- evidence:

### T-110 Event chain with source_version
- status: todo
- spec: REQ-LNS-012
- owner: lead-backend-engineer
- depends: T-109, T-102, T-106
- write_scope: services/core/, services/pulse/, services/ml/, tests/unit/test_event_chain.py
- context: libs/contracts/events.py, docs/adr/ADR-IMT-0002-LENS.md section 2.6
- size: M
- acceptance: `pytest -q tests/unit/test_event_chain.py`
- note: wire signal.tagged, signal.scored, company.matched, signal.explained, funnel.gate_decision, project.pipeline_updated, and project.forecast_updated so every event carries `source_version` and the chain can be traced. Contracts are additive: new modules only.
- evidence:

### T-111 Streamlit validation page
- status: todo
- spec: REQ-LNS-013
- owner: second-backend-engineer
- depends: T-108, T-056
- write_scope: services/streamlit/pages/, tests/unit/test_streamlit_validation.py
- context: services/streamlit/, docs/adr/ADR-IMT-0002-LENS.md section 2.7
- size: M
- acceptance: `pytest -q tests/unit/test_streamlit_validation.py` (Streamlit AppTest)
- note: page `9_Validation.py` with V1 to V6 per project, `measured_at`, `source_version`, and per-card degradation; add V6 to the Ops page.
- evidence:

### T-112 Layer L2 review pack
- status: todo
- spec: REQ-LNS-001
- owner: architecture-reviewer
- depends: T-110, T-111, T-103, T-104, T-105
- write_scope: docs/runs/
- context: SPEC.md (sections 4.2 and 6), TASKS.md
- size: S
- acceptance: `make static`, `make test`, and `python ops/check_traceability.py` pass
- note: check every L2 requirement against its evidence, list what depends on the owner (D-06 checklists), and append the L2 section to `docs/runs/RUN-<run-id>.md`. Do not start any layer L3 work.
- evidence:
