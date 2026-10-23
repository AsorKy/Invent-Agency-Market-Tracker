# Invent Market Tracker - Tasks

Status: ACTIVE v6 (2026-10-19). Generated from `SPEC.md` v3.1 for run 1 (layer L1, ADR-IMT-0001). Task IDs are never reused. v4 adds the hardening tasks T-090 to T-097 (event tenant, single relay, SQL consistency tests, unique indexes, mapping verification, dbt mart naming) and regroups the remaining work into parallel sessions (see Waves in run r1-c). v6 adds the follow-up tasks T-081 to T-089, T-098, and T-120 to T-127 found in the r1-c and r1-d run reports, and plans run r1-d2 (see Waves in run r1-d2).

Each task cites the requirements in `SPEC.md` that it satisfies. The run protocol (branch, commit, push, evidence, blocked handling) is defined in `AGENTS.md`. Only L1 work is listed; L2 and L3 tasks are generated when their runs open.

## What run 1 must deliver

- A tested backend (unit tests for every service and shared module, an offline pipeline test, and an integration tier for the owner to run) that is ready for review and promotion.
- `make invent-tracker` starting the whole application with seeded demo data.
- A switch to live data by editing `.env` only (`INGEST_MODE=live`, Bright Data credentials and dataset IDs), checked with `make doctor` and `make verify-live`, with records reaching the public API at `/api/v1` and the OpenAPI contract in `docs/api/openapi.json` for the frontend.

## Before launching (owner)

1. Optional: real sample responses under `tests/fixtures/brightdata/` verify the field mappings (decision D-13). You do not need to collect them by hand: mappings stay `unverified` until `make verify-live` captures redacted samples from the first real snapshot and `ops/verify_mappings.py` (T-096) passes on them.
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
| r1-c | Hardening T-090 to T-097 plus the second half of the services: T-030 to T-035, T-045, T-046, T-048, T-049, T-058 (five parallel sessions) | 19 tasks | about 58 USD |
| r1-d | Platform, three sessions (done): T-060 to T-063, T-065, T-070 to T-073, T-077 | 11 tasks | about 24 USD |
| r1-d2 | Follow-ups and launch tooling, seven parallel sessions: T-064, T-074 to T-076, T-078, T-079, T-081, T-083 to T-089, T-098, T-120 to T-122, T-124 to T-127 (T-082 is reserved for the row level security decision) | 22 tasks | about 62 USD |
| r1-e | Security migration and review: T-123, T-080 | 2 tasks | about 6 USD |
| r1-f | Long wave, four parallel sessions: layer L2 analytics T-100 to T-106 (one chain), schema T-129 and T-107, lens and cleanup T-101, T-133, T-134, platform T-132, T-130, T-131 | 14 tasks | about 55 USD |
| r1-g | Long wave, four parallel sessions: layer L2 validation chain T-108, T-109, T-111, T-110, T-112; pulse endpoints and attribution storage T-135, T-136; ml and brief gaps T-139, T-141, T-137, T-138; platform T-140, T-143 | 14 tasks | about 55 USD |
| r1-h | Long wave, five parallel sessions: score weights through the core API and the score stored in core T-144, T-145; funnel and validation served end to end T-146, T-147, T-148; event chain and pulse project writers T-149, T-152; ml publisher tenant and explanations T-150, T-151; compose, images, runbook, registry and SPEC lines T-153, T-154, T-156, T-157, T-155 | 14 tasks | about 100 USD |
| r1-i | Short wave, three parallel sessions: funnel and validation served end to end T-146 to T-148 (unblocked, KILL sets the status killed); event chain producers T-159; compose core URL for ml, schedule tenant, e2e check of stored scores T-158, T-160, T-161 | 7 tasks | about 40 USD |
| r1-j | Short wave, three parallel sessions: real Postgres tests for the new SQL and funnel decision lock T-162, T-163; e2e with the event chain on and ml feature backfill T-164, T-165; event chain runbook and SPEC tasks lines T-166, T-167 | 6 tasks | about 40 USD |
| r1-k | Short wave, two parallel sessions: qualification applies the tenant of the score event T-168; core serves score features per tenant, ML backfills old score rows, runbook step T-165, T-169 | 3 tasks | about 40 USD |
| r1-l | Short wave, two parallel sessions: loopback ports and smoke task closure T-170, T-172; e2e proof of auto qualified leads and the backfill, doctor pipeline report T-171, T-173 | 4 tasks | about 30 USD |
| r1-m | Wave, three parallel sessions: verify-live in the Compose network and the versioned feature baseline T-186, T-178; production refusal of seed and verify_live tokens and the n8n mint credential T-184, T-185; typed chain payloads, parent ids, the ml outbox with one shared relay and the pulse stage projection T-174 to T-177 | 8 tasks | about 65 USD |
| r1-n | Wave, two parallel sessions: row level security on signal, location and lead outcome T-082, then the pulse refresh allow list T-189 and the test lint T-190; validation inputs served end to end T-179, T-180, funnel per project T-181, and real parent ids in the production callers T-188 | 7 tasks | about 70 USD |
| r2 | Continuation with ADR-0002: T-100 to T-112, only if budget remains after T-080; run as r1-f and r1-g | 13 tasks | about 38 USD |

Total estimate for run 1 (smoke to r1-d): about 197 USD (183 USD of the v3 plan plus about 14 USD for T-090 to T-097 and about 30 USD for the v6 follow-ups), plus about 38 USD for the optional ADR-0002 continuation, to be corrected after the smoke run shows real cost per task. Blocked tasks do not consume budget. Set the session budget of each run to about 1.5 times its estimate.

### Lanes in run r1-b

Multiagent work starts only after T-014 (contracts frozen). Use one coordinator plus at most three lanes with disjoint write sets; tasks inside a lane run in order.

| Lane | Tasks | Writes |
| --- | --- | --- |
| A (lead-backend-engineer) | T-020 to T-027, T-030 to T-035 | services/core, services/identity, services/gateway, services/brief |
| B (second-backend-engineer) | T-040 to T-049 | services/ingest, services/mocks, orchestration/n8n, libs/domain/source_mappings |
| C (second-backend-engineer) | T-050 to T-059 | services/pulse, services/analytics (delete), services/ml, services/streamlit, transform |

Cross-lane dependencies (for example T-045 needs T-020) are resolved by the coordinator: take the first task whose `depends` are all `done`.

### Waves in run r1-c

Five sessions run in parallel, each with its own coordinator and branch `agent/<run-id>`. Their write sets are disjoint, so the pull requests merge without conflicts (only `pyproject.toml` extras and this file auto-merge). A session touches only the `write_scope` of its own tasks.

| Session | Tasks (in order) | Writes | Cap |
| --- | --- | --- | --- |
| r1-c-core | T-090, T-091, T-092, T-035, T-049 | services/core, orchestration/n8n, tests | 18 USD |
| r1-c-data | T-093, T-094, T-095, T-097 | migrations, transform, tests, pyproject | 24 USD |
| r1-c-edge | T-030, T-031, T-032 | services/identity, services/gateway | 22 USD |
| r1-c-brief | T-033, T-034, T-058 | services/brief, services/streamlit/pages | 16 USD |
| r1-c-ingest | T-045, T-046, T-048, T-096 | services/ingest, libs/domain/source_mappings, ops/verify_mappings.py, tests/fixtures | 20 USD |

Rules: SQL written by any session must pass `tests/unit/test_sql_consistency.py` once T-094 is on `main`; the owner merges r1-c-data first, re-runs the other branches' CI, and any SQL finding becomes a short fix session. Decisions behind T-090 to T-097 are D-16 to D-19 in `SPEC.md`.

### Waves in run r1-d2

Seven sessions run in parallel after the r1-d pull requests are merged (the r1-d sessions r1-d-core2, r1-d-data2, and r1-d-ingest2 found no tasks because v5 was not merged yet; their work is replanned here). Write sets are disjoint; merge `r1-d2-mlpulse` after `r1-d2-core`, because both touch `services/core/app/`.

| Session | Tasks (in order) | Writes | Cap |
| --- | --- | --- | --- |
| r1-d2-core | T-084, T-085 | libs/common/settings.py, .env.example, services/core | 12 USD |
| r1-d2-data | T-083, T-086, T-087, T-122, T-081 | migrations, services/brief, tests/unit/support_sql.py | 26 USD |
| r1-d2-ingest | T-120, T-089, T-098 | services/ingest, services/mocks | 20 USD |
| r1-d2-mlpulse | T-121, T-088, T-124 | services/ml, services/pulse, services/core/app (events) | 22 USD |
| r1-d2-ops | T-125 | docker-compose.yml, deploy | 10 USD |
| r1-d2-qa | T-126, T-127 | new test files only | 20 USD |
| r1-d2-launch | T-074, T-075, T-076, T-078, T-079, T-064 | Makefile, ops, tests/integration, docs/runbooks, ci.yml (patch) | 30 USD |

## If the budget is not exhausted: continue with ADR-0002

The session does not stop when T-080 (the run 1 review pack) is `done`. If budget remains, it continues with the layer L2 tasks T-100 to T-112, which implement ADR-IMT-0002-LENS (`REQ-LNS-*`). They all depend on T-080, so the normal task loop reaches them in order. Rules:

- Start T-100 only when T-080 is `done`; never start an L2 task earlier, even if its own dependencies are met.
- Stop at the end of T-112 or when the budget runs out. Never start layer L3 (ADR-IMT-0003) in this session.
- The funnel engine (T-109) ships with example checklists only; the owner replaces them (decision D-06).
- The last section of the run report states whether the L2 tasks ran, which are `done`, and which were left `todo` for a later session.

## Tasks

### T-000 Smoke test
- status: done
- spec: REQ-QA-001
- owner: coordinator
- depends: none
- write_scope: docs/runs/
- context: AGENTS.md
- size: S
- acceptance: `make static`
- note: create `docs/runs/RUN-smoke.md` with one line per check performed (git branch, commit, push, `make static` result). If `make static` cannot run because tools are missing, record exactly which ones. Also record the Python version and whether `pip install .[dev]` works.
- evidence: docs/runs/RUN-smoke.md written; make static OK, make test 1607 passed, Python 3.12.3, pip install of the extras works.

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
- status: done
- spec: REQ-QA-002, REQ-GW-001, REQ-GW-002
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_gateway.py
- context: services/gateway/app/main.py, libs/common/settings.py, libs/common/security.py
- size: M
- acceptance: `pytest -q tests/unit/test_gateway.py`
- note: use the in-process ASGI client and fake upstreams. No Redis or Postgres. Cover `/health`, `/ready` with a failing upstream, 404 for non-public paths, edge secret enforcement (404, never 403), double-submit CSRF (403 `csrf_token_invalid`), the rate limiter with the fake Redis, and `X-Request-ID` propagation. Report production defects instead of changing production code.
- evidence: `pytest -q tests/unit/test_gateway.py` -> 1 skipped (fastapi/httpx/fakeredis missing offline); ~45 cases written from code reading, unverified until CI

### T-005 Outbox relay and idempotent consumer tests
- status: done
- spec: REQ-QA-002, REQ-EVT-001, REQ-EVT-002
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_outbox.py
- context: services/core/app/outbox_relay.py, services/core/app/worker.py, libs/common/redis_streams.py, libs/contracts/events.py
- size: L
- acceptance: `pytest -q tests/unit/test_outbox.py`
- note: use fakes for the database and Redis. Verify unpublished events are published once marked, retried on failure, and that a repeated event ID is applied once by each consumer (ml and the pulse consumer under its current name).
- evidence: `pytest -q tests/unit/test_outbox.py` -> 1 skipped (fakeredis/fastapi stack missing offline); written from code reading, unverified until CI

### T-006 Ingest webhook signature tests
- status: done
- spec: REQ-QA-002, REQ-SEC-002
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_ingest_webhook.py
- context: services/ingest/app/main.py, libs/common/security.py, libs/common/settings.py
- size: M
- acceptance: `pytest -q tests/unit/test_ingest_webhook.py`
- note: cover a valid signature, a wrong signature, a stale timestamp, and a future timestamp outside the window.
- evidence: `pytest -q tests/unit/test_ingest_webhook.py` -> 12 passed, 6 skipped (endpoint tests need fastapi stack, unverified offline)

### T-007 Add the unit job to CI
- status: done
- spec: REQ-QA-001, REQ-CI-001
- owner: devops-engineer
- depends: T-003, T-004, T-005, T-006
- write_scope: `.github/workflows/ci.yml`
- context: .github/workflows/ci.yml, Makefile
- size: S
- acceptance: the workflow file parses as YAML and contains a `unit` job with `needs: static` that runs `make test`
- evidence: BLOCKED: change is ready (docs/runs/T-007-ci-unit-job.patch, `git apply` it) but the push token lacks the `workflow` scope, so GitHub rejects any commit touching .github/workflows/; owner must apply it

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
- status: done
- spec: REQ-DAT-006, REQ-DAT-007, REQ-EXT-003
- owner: database-engineer
- depends: T-008
- write_scope: ops/check_migrations.py, ops/schemas.py, ops/bootstrap_roles.py, ops/bootstrap_roles.sql.example, tests/unit/test_migration_guard.py
- context: ops/check_migrations.py, ops/bootstrap_roles.sql.example, docs/adr/ADR-IMT-0001-ARCH.md sections 8.2, 17.3, E2.1
- size: M
- acceptance: `pytest -q tests/unit/test_migration_guard.py` and `python ops/check_migrations.py`
- note: one list (`ops/schemas.py`) drives the guardian and the role bootstrap. The guardian rejects DROP, RENAME, ALTER TYPE, and NOT NULL without default outside `contract_` files, and a `contract_` file in the same commit as application code (use a `--base` git ref option, tested with fixtures). The bootstrap enables pgvector, postgis, pg_cron, and pg_trgm idempotently and creates `_owner` and `_app` roles with no cross-schema grants. A test adds a fake schema to the list and expects both to accept it.
- evidence: `pytest -q tests/unit/test_migration_guard.py tests/unit/test_schema_catalog.py` -> 29 passed, 1 xfailed; `python ops/check_migrations.py` -> OK

### T-011 Event catalog, envelope, and tolerant consumers
- status: done
- spec: REQ-EVT-005, REQ-EXT-001
- owner: lead-backend-engineer
- depends: T-001
- write_scope: libs/contracts/events.py, libs/common/events_dispatch.py, tests/unit/test_contracts.py
- context: libs/contracts/events.py, libs/common/redis_streams.py, docs/adr/ADR-IMT-0001-ARCH.md sections 7.3, E1.4, E2.3
- size: M
- acceptance: `pytest -q tests/unit/test_contracts.py`
- note: define the L1 events and the common envelope (event_id, event_type, aggregate_type, aggregate_id, version, source_version, created_at, optional replay), list the reserved L2 and L3 event names without handlers, and add one dispatch helper that acknowledges and logs a single line for unknown types. Keep existing field names compatible. Payloads are never logged in full.
- evidence: `pytest -q tests/unit/test_contracts.py` -> 4 passed, 1 skipped (envelope test needs pydantic, unverified offline)

### T-012 Process supervisor, health, and readiness
- status: done
- spec: REQ-EVT-006, REQ-RES-001
- owner: lead-backend-engineer
- depends: T-001
- write_scope: libs/common/supervise.py, tests/unit/test_supervise.py, tests/unit/test_health.py
- context: libs/common/supervise.py, services/*/procs.toml, services/*/app/main.py, docs/adr/ADR-IMT-0001-ARCH.md sections 6.3, 14.1
- size: M
- acceptance: `pytest -q tests/unit/test_supervise.py tests/unit/test_health.py`
- note: the supervisor must stop all children and exit 1 when one dies and must treat a missing heartbeat (90 s) as dead; implement what is missing. Health tests import every service app and check `/health` (always 200) and `/ready` (503 when its dependency fake fails). Do not edit service `main.py` files; report services that lack the endpoints so their lane fixes them.
- evidence: `pytest -q tests/unit/test_supervise.py tests/unit/test_health.py` -> 12 passed, 1 skipped (health needs fastapi, unverified offline); identity, streamlit, mocks lack a real /ready (xfail)

### T-013 Datagen determinism and snapshot-shaped output
- status: done
- spec: REQ-DAT-008
- owner: second-backend-engineer
- depends: T-001
- write_scope: datagen/, tests/unit/test_datagen.py
- context: datagen/builder.py, datagen/__main__.py, ops/seed/README.md
- size: M
- acceptance: `pytest -q tests/unit/test_datagen.py`
- note: two runs with the same seed give identical output (hash compare); the default dataset has 200 companies across Medellin, Bogota, and Cali, about 1,500 signals over 180 days, and 50 leads with outcomes. Add `--as-snapshots` that emits provider-shaped records for maps and computrabajo (for the seed and the offline pipeline test). Only company data; no personal data.
- evidence: `pytest -q tests/unit/test_datagen.py` -> 4 passed; mypy datagen clean; snapshot field names are assumed (unverified mappings)

### T-014 Freeze shared contracts
- status: done
- spec: REQ-EVT-005, REQ-EXT-001, REQ-EXT-004
- owner: architecture-reviewer
- depends: T-011, T-015, T-018
- write_scope: tests/unit/test_contract_snapshot.py, tests/unit/snapshots/, docs/contracts-freeze.md
- context: libs/contracts/, libs/common/settings.py, migrations/
- size: S
- acceptance: `pytest -q tests/unit/test_contract_snapshot.py`
- note: snapshot the JSON schemas of every model in `libs/contracts`, the event envelope, the settings field names, and the schema and table catalog. After this task, edits to existing files in `libs/contracts/`, `libs/common/settings.py`, and `migrations/` need the coordinator. Service tasks may add new modules under `libs/contracts/` and extend the snapshot for their own module only. Multiagent lanes start after this task.
- evidence: `pytest -q tests/unit/test_contract_snapshot.py` -> 4 passed, 1 skipped; pydantic model_schemas.json snapshot NOT generated (pydantic unavailable offline): run `UPDATE_SNAPSHOTS=1 pytest tests/unit/test_contract_snapshot.py` in CI and commit it

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
- status: done
- spec: REQ-ARC-002
- owner: architecture-reviewer
- depends: T-001
- write_scope: .codex/agent-registry.yaml, .codex/agents/, tests/unit/test_registry.py
- context: AGENTS.md (Agent roster), .codex/agent-registry.yaml, SPEC.md section 3.1
- size: S
- acceptance: `pytest -q tests/unit/test_registry.py`
- note: add owners for identity, brief, pulse, streamlit, and mocks as in SPEC section 3.1, remove `analytics`, and keep the registry, the roster in `AGENTS.md`, and `.codex/agents/` in sync. The test checks every directory under `services/` has an owner.
- evidence: `pytest -q tests/unit/test_registry.py` -> 5 passed; AGENTS.md roster synced by coordinator

### T-017 Guards: workflows, secrets, and static wiring
- status: done
- spec: REQ-ORC-001, REQ-SEC-004
- owner: devops-engineer
- depends: T-002
- write_scope: ops/check_configuration.py, ops/check_workflows.py, Makefile, tests/unit/test_guards.py
- context: ops/check_configuration.py, Makefile, orchestration/n8n/
- size: M
- acceptance: `pytest -q tests/unit/test_guards.py` and `make static`
- note: add a workflow checker (no code nodes, no secret-like values, no business logic nodes) and wire `ops/check_traceability.py` and the workflow checker into `make static`. Add a secret-pattern scan over tracked text files that reports file names and counts only, never matched text.
- evidence: `pytest -q tests/unit/test_guards.py` -> 7 passed; all check_*.py OK, ruff clean, `mypy ops` clean; `make static` fully runnable only in CI (py3.11 sandbox cannot parse PEP 695 in libs/common/retry.py)

### T-018 Schema additions for run 1 (expand migration)
- status: done
- spec: REQ-COR-001, REQ-DAT-005, REQ-EXT-004
- owner: database-engineer
- depends: T-008, T-010
- write_scope: migrations/, tests/unit/test_schema_catalog.py
- context: SPEC.md section 3.2, migrations/, docs/adr/ADR-IMT-0001-ARCH.md sections 5.1, 5.4, E2.1
- size: L
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_schema_catalog.py`
- note: bring the migrations in line with SPEC section 3.2 in one new expand migration: every listed table exists in its owning schema; `analytics` objects live in `pulse`; brief tables live in `brief`; each consumer schema has `processed_event`; add `ingest.pending_snapshot`, `ingest.failed_record`, `core.job`, `core.upload_blob`, `core.tenant_config` (with ETag version) plus a history table, `core.outbox_dead_letter`, `core.audit_log`, `core.lead_activity` (channel, source), and the lead columns `source`, `city`, `service_line`, and nullable `holdout`; enable RLS and tenant policies on company, lead, and project. The test parses the SQL against the catalog.
- evidence: `python ops/check_migrations.py` -> OK; `pytest -q tests/unit/test_schema_catalog.py tests/unit/test_migration_guard.py tests/unit/test_taxonomy.py` -> 43 passed, 1 xfailed (SQL never run against real Postgres)

### T-020 Company upsert API with history
- status: done
- spec: REQ-COR-007
- owner: lead-backend-engineer
- depends: T-014, T-018
- write_scope: services/core/, libs/contracts/companies.py, tests/unit/test_company_upsert.py
- context: services/core/app/main.py, services/core/app/repository.py, libs/contracts/events.py, docs/adr/ADR-IMT-0001-ARCH.md section 5.3
- size: M
- acceptance: `pytest -q tests/unit/test_company_upsert.py`
- note: implement `POST /internal/core/companies/upsert`, SCD2 history, version bump, and the event in the same transaction. Test with a fake repository that records the transaction boundary; also cover repeating the same payload.
- evidence: `pytest -q tests/unit/test_company_upsert.py` -> 11 passed; make static OK; make test 263 passed. CompanyUpsert lives in services/core/app/companies.py (not libs/contracts, frozen snapshot); SCD2 history goes to core.audit_log (no history table in schema).

### T-021 Lead model, state machine, and list filters
- status: done
- spec: REQ-COR-002
- owner: lead-backend-engineer
- depends: T-020
- write_scope: services/core/, libs/domain/pipeline.py, libs/contracts/leads.py, tests/unit/test_lead_state.py
- context: libs/domain/pipeline.py, services/core/app/main.py, services/core/app/repository.py, docs/adr/ADR-IMT-0001-ARCH.md section E1.1
- size: L
- acceptance: `pytest -q tests/unit/test_lead_state.py`
- note: valid transitions per the E1.1 diagram, `expected_version` with 409 and the current state, `Idempotency-Key` on create, filters (state, service_line, city, segment, min_score) with keyset cursor, `GET /leads/{id}`, and the kanban read model. Pure transition logic lives in `libs/domain/pipeline.py` with at least 90 percent coverage.
- evidence: `pytest -q tests/unit/test_lead_state.py` -> 11 passed; pipeline.py coverage 100%; make static OK; make test 274 passed. Models in services/core/app/leads.py (contracts frozen); no status-change event type exists.

### T-022 Outcomes, activities, project conversion, and projects read
- status: done
- spec: REQ-COR-003, REQ-COR-005
- owner: lead-backend-engineer
- depends: T-021
- write_scope: services/core/, libs/contracts/projects.py, tests/unit/test_outcomes.py, tests/unit/test_lead_actions.py
- context: services/core/app/main.py, services/core/app/repository.py, docs/adr/ADR-IMT-0001-ARCH.md sections 3 (P7), E1.1
- size: L
- acceptance: `pytest -q tests/unit/test_outcomes.py tests/unit/test_lead_actions.py`
- note: outcome capture (contact, response, meeting, close, spam) emitting `lead.outcome_recorded`; activity logging; lead to project conversion in one transaction; replace the 501 on `GET /internal/core/projects` with a real read model and add `POST /internal/core/projects`.
- evidence: `pytest -q tests/unit/test_outcomes.py tests/unit/test_lead_actions.py` -> 16 passed; make static OK; make test 290 passed. Models in services/core/app/actions.py (contracts frozen).

### T-023 Tenant configuration with ETag and reserved keys
- status: done
- spec: REQ-COR-004, REQ-EXT-002
- owner: lead-backend-engineer
- depends: T-021
- write_scope: services/core/, libs/contracts/tenant_config.py, tests/unit/test_tenant_config.py
- context: services/core/app/main.py, docs/adr/ADR-IMT-0001-ARCH.md sections E1.1, E1.6, E2.3
- size: M
- acceptance: `pytest -q tests/unit/test_tenant_config.py`
- note: Pydantic model for `brief_rules` and the reserved `funnel_rules` with `geo_overrides`; `GET` and `PUT /internal/core/admin/tenant-config` with `If-Match` (412 on stale); previous version stored in the history table; emits `tenant.config_updated`. Unknown top-level keys are rejected; reserved keys are accepted without a migration.
- evidence: `pytest -q tests/unit/test_tenant_config.py` -> 9 passed; make static OK; make test 299 passed. Models in services/core/app/tenant_config.py (contracts frozen).

### T-024 Automatic qualification consumer
- status: done
- spec: REQ-COR-008
- owner: lead-backend-engineer
- depends: T-023
- write_scope: services/core/, tests/unit/test_auto_qualification.py
- context: services/core/app/worker.py, libs/domain/pipeline.py, docs/adr/ADR-IMT-0001-ARCH.md sections E1.1, E1.6
- size: M
- acceptance: `pytest -q tests/unit/test_auto_qualification.py`
- note: consume `lead.score_updated` with the inbox pattern; create or advance leads by the tenant thresholds; never move backward or override a human-set status; write `audit_log`; emit `lead.created` for new leads.
- evidence: `pytest -q tests/unit/test_auto_qualification.py` -> 12 passed; make static OK; make test 311 passed.

### T-025 Dead letter queue and republish
- status: done
- spec: REQ-EVT-003, REQ-EVT-004
- owner: lead-backend-engineer
- depends: T-020
- write_scope: services/core/, tests/unit/test_dead_letter.py, tests/unit/test_republish.py
- context: services/core/app/outbox_relay.py, libs/common/retry.py, docs/adr/ADR-IMT-0001-ARCH.md sections 7.5, E1.4
- size: M
- acceptance: `pytest -q tests/unit/test_dead_letter.py tests/unit/test_republish.py`
- note: backoff 10 s, 30 s, 90 s, 5 min, 15 min then `core.outbox_dead_letter` with an alert log line; `python -m services.core.app.republish --target <pulse|brief|ml> --since <date>` replays with `replay: true`, requires `--confirm`, and blocks writes to the target while running (lock key).
- evidence: `pytest -q tests/unit/test_dead_letter.py tests/unit/test_republish.py` -> 28 passed (incl. test_outbox); make static OK; make test 324 passed. NOTE: dead_letter relay not wired into compose/outbox_relay.main (test_outbox.py pins old relay); needs devops+qa follow-up.

### T-026 CSV imports as background jobs
- status: done
- spec: REQ-ING-005
- owner: lead-backend-engineer
- depends: T-020
- write_scope: services/core/, libs/contracts/imports.py, tests/unit/test_imports.py
- context: services/core/app/main.py, services/core/app/worker.py, docs/adr/ADR-IMT-0001-ARCH.md section E1.7
- size: L
- acceptance: `pytest -q tests/unit/test_imports.py`
- note: implement the 202 plus job flow, limits (10 MB, 100,000 rows, 3 active jobs per tenant with 429), Polars chunked parsing with Pydantic validation, upsert by `row_sha256`, events per changed aggregate, and blob cleanup. Add `polars` to the `analytics` extra only if it is not already a base dependency; ask the coordinator for `pyproject.toml` changes.
- evidence: `pytest -q tests/unit/test_imports.py` -> 15 passed; make static OK; make test 339 passed. stdlib csv parser behind ChunkParser (polars declared in pyproject but not installed); raw CSV body, gateway must unwrap multipart.

### T-027 Retention purge and opt-out
- status: done
- spec: REQ-CMP-001
- owner: lead-backend-engineer
- depends: T-020
- write_scope: services/core/, tests/unit/test_retention.py, docs/compliance/processing-activities.md
- context: docs/adr/ADR-IMT-0001-ARCH.md section 13.4, services/core/app/repository.py
- size: M
- acceptance: `pytest -q tests/unit/test_retention.py`
- note: purge companies 12 months after their last signal (job callable from a scheduler, dry-run option), idempotent opt-out delete endpoint, a guard that rejects storing personal identifiers (document numbers, private phones), and a short processing-activities record in English.
- evidence: `pytest -q tests/unit/test_retention.py` -> 16 passed; make static OK; make test 355 passed.

### T-030 Identity service: Django project, RBAC, JWKS
- status: done
- spec: REQ-IDN-001
- owner: lead-backend-engineer
- depends: T-014, T-018
- write_scope: services/identity/, pyproject.toml (identity extra only), tests/unit/test_identity_rbac.py
- context: services/identity/app/main.py, services/identity/README.md, docs/adr/ADR-IMT-0001-ARCH.md sections E1.1, E1.2
- size: L
- acceptance: `pytest -q tests/unit/test_identity_rbac.py`
- note: Django plus DRF in `services/identity`, tables in the `identity` schema, models for user, tenant, role (admin, strategist, intake, bd_rep, analyst), and scope (city, service line), an RS256 key pair with a JWKS endpoint, service tokens, and the Django admin only on the internal network. Tests use SQLite settings and the Django test client. Decision D-14: if Django proves impractical in the sandbox after three focused attempts, mark `blocked`.
- evidence: `pytest -q tests/unit/test_identity_rbac.py` -> 7 passed; make static and make test pass

### T-031 Identity sessions, CSRF, local profile, and dev users
- status: done
- spec: REQ-IDN-002
- owner: lead-backend-engineer
- depends: T-030, T-015
- write_scope: services/identity/, tests/unit/test_identity_session.py
- context: services/identity/, libs/common/security.py, docs/adr/ADR-IMT-0001-ARCH.md sections 13.1, E1.1
- size: M
- acceptance: `pytest -q tests/unit/test_identity_session.py`
- note: login sets the cookie attributes of REQ-IDN-002 in the production profile; the local profile drops the `__Host-` prefix and `Secure`; sliding renewal, 30 minutes of inactivity, 12 hours absolute, logout invalidation. A management command creates one development user per role with passwords taken from the environment (never hard-coded).
- evidence: `pytest -q tests/unit/test_identity_session.py` -> 9 passed; make static and make test pass

### T-032 Gateway: JWT/JWKS, route table, tenant propagation, local profile
- status: done
- spec: REQ-GW-003, REQ-IDN-003, REQ-API-002, REQ-API-003
- owner: lead-backend-engineer
- depends: T-031, T-020, T-021
- write_scope: services/gateway/, tests/unit/test_gateway_auth.py, tests/unit/test_gateway_routes.py, tests/unit/test_tenant_isolation.py, tests/unit/test_profiles.py
- context: services/gateway/app/main.py, libs/common/security.py, docs/adr/ADR-IMT-0001-ARCH.md sections 6.4, E1.8, E1.10, 12.4
- size: L
- acceptance: `pytest -q tests/unit/test_gateway_auth.py tests/unit/test_gateway_routes.py tests/unit/test_tenant_isolation.py tests/unit/test_profiles.py`
- note: verify JWTs against the identity JWKS (cached 10 minutes), propagate tenant and role headers, enforce tenant isolation (404 across tenants), implement the route table of REQ-API-002 (pulse instead of analytics; 404 `not_in_layer` for L2 and L3 resources), add the CORS allowlist from `FRONTEND_ORIGIN` for the local profile, apply per-route upstream timeouts, and return 503 only for routes whose upstream `/ready` is red (poll every 10 s). Remove every leftover reference to the retired `analytics` service from the gateway code and settings, and make sure tenant and role headers are set only by the gateway after JWT validation, never copied from the client request.
- evidence: the four gateway/tenant/profile test files -> 71 passed; make static and make test pass

### T-033 Brief cache, single-flight, and rule-based fallback
- status: done
- spec: REQ-BRF-001, REQ-BRF-002
- owner: lead-backend-engineer
- depends: T-014, T-018
- write_scope: services/brief/, tests/unit/test_brief_cache.py, tests/unit/test_brief_singleflight.py
- context: services/brief/app/main.py, services/brief/app/single_flight.py, services/brief/app/rule_based.py, docs/adr/ADR-IMT-0001-ARCH.md sections 10.2, E1.6
- size: L
- acceptance: `pytest -q tests/unit/test_brief_cache.py tests/unit/test_brief_singleflight.py`
- note: implement the three states (hit, stale, degraded) with the `X-Brief-Cache` header, first-read blocking generation, stale-while-revalidate, the Redis lock (SET NX PX 30000, Lua release), the 5-second summarizer timeout with rule fallback, and 503 `db_unavailable` when core is unreachable. The summarizer is a replaceable interface; ship the rule-based one (decision D-09).
- evidence: `pytest -q tests/unit/test_brief_cache.py tests/unit/test_brief_singleflight.py` -> 12 passed; make static and make test pass

### T-034 Brief alert rules per tenant
- status: done
- spec: REQ-BRF-003
- owner: lead-backend-engineer
- depends: T-033, T-023
- write_scope: services/brief/, tests/unit/test_brief_rules.py
- context: services/brief/app/rule_based.py, libs/contracts/tenant_config.py, docs/adr/ADR-IMT-0001-ARCH.md section E1.1
- size: M
- acceptance: `pytest -q tests/unit/test_brief_rules.py`
- note: alert codes and severities from E1.1, thresholds and weights from `brief_rules`, disabled codes, and lazy invalidation on `tenant.config_updated`.
- evidence: `pytest -q tests/unit/test_brief_rules.py` -> passed; make static and make test pass

### T-040 Raw snapshot ingestion and change detection
- status: done
- spec: REQ-ING-001
- owner: second-backend-engineer
- depends: T-014, T-018
- write_scope: services/ingest/, tests/unit/test_ingest_snapshot.py
- context: services/ingest/app/main.py, services/ingest/app/repository.py, docs/adr/ADR-IMT-0001-ARCH.md sections 4.1, 4.3
- size: M
- acceptance: `pytest -q tests/unit/test_ingest_snapshot.py`
- note: store snapshots with sha256 and `UNIQUE(source, external_id, sha256)`, create new signals only for changed content, enqueue one processor job per snapshot, and keep the webhook response fast (202).
- evidence: `pytest -q tests/unit/test_ingest_snapshot.py` -> 8 passed; make static and make test pass

### T-041 Budget caps and failed fetch handling
- status: done
- spec: REQ-ING-002
- owner: second-backend-engineer
- depends: T-040
- write_scope: services/ingest/, tests/unit/test_ingest_budget.py
- context: services/ingest/app/repository.py, docs/adr/ADR-IMT-0001-ARCH.md section 4.3
- size: M
- acceptance: `pytest -q tests/unit/test_ingest_budget.py`
- note: monthly cap per source in `ingest.budget` checked before any trigger; failures stored in `ingest.failed_fetch` with exponential backoff and dead letter after 5 failures.
- evidence: `pytest -q tests/unit/test_ingest_budget.py` -> 7 passed; make static and make test pass

### T-042 Connector interface, mapping files, and Bright Data mock
- status: done
- spec: REQ-ING-004, REQ-ING-007
- owner: second-backend-engineer
- depends: T-040, T-015
- write_scope: services/ingest/, services/mocks/, libs/domain/source_mappings/, tests/fixtures/, tests/unit/test_connectors.py, tests/unit/test_mocks.py
- context: services/ingest/app/, services/mocks/app/main.py, docs/adr/ADR-IMT-0001-ARCH.md sections 4.1, 4.4
- size: L
- acceptance: `pytest -q tests/unit/test_connectors.py tests/unit/test_mocks.py`
- note: define `SourceConnector` (trigger, fetch, normalize), the YAML mapping format, mapping files for maps and computrabajo, and the mock connector. Extend the Flask mock to emulate trigger, progress, snapshot download, latency, failure rate, and signed webhooks. If `tests/fixtures/brightdata/` contains real samples (decision D-13), build mappings from them; otherwise mark each mapping `unverified: true` and list it for the run report.
- evidence: `pytest -q tests/unit/test_connectors.py tests/unit/test_mocks.py` -> 29 passed; make static and make test pass; mappings unverified (no fixtures/brightdata)

### T-043 Bright Data live connector and polling fallback
- status: done
- spec: REQ-ING-004, REQ-ING-008, REQ-RUN-004
- owner: second-backend-engineer
- depends: T-042, T-041
- write_scope: services/ingest/, tests/unit/test_connectors.py, tests/unit/test_ingest_poll.py, tests/unit/test_source_config.py
- context: services/ingest/app/, libs/common/settings.py (read only), docs/adr/ADR-IMT-0001-ARCH.md sections 4.1, 4.3
- size: L
- acceptance: `pytest -q tests/unit/test_connectors.py tests/unit/test_ingest_poll.py tests/unit/test_source_config.py`
- note: implement the Bright Data Datasets API client with `httpx` (Bearer token, trigger with the notify URL built from `PUBLIC_WEBHOOK_BASE_URL`, progress, snapshot download, retry with backoff only for idempotent calls). Track triggers in `ingest.pending_snapshot` and poll when `INGEST_POLL_ENABLED`; deduplicate against webhook delivery. `INGEST_MODE` selects mock or live per source; a live source without credentials fails at startup naming the variable. Tests use `httpx.MockTransport` only.
- evidence: `pytest -q tests/unit/test_connectors.py tests/unit/test_ingest_poll.py tests/unit/test_source_config.py` -> 48 passed; make static/test pass

### T-044 Entity resolution against RUES (mock lookup)
- status: done
- spec: REQ-ING-003
- owner: second-backend-engineer
- depends: T-040
- write_scope: libs/domain/entity_resolution.py, services/ingest/, tests/unit/test_entity_resolution.py
- context: libs/domain/entity_resolution.py, services/ingest/app/repository.py, docs/adr/ADR-IMT-0001-ARCH.md section 5.3
- size: L
- acceptance: `pytest -q tests/unit/test_entity_resolution.py`
- note: thresholds 0.9 and 0.6, `entity_match_log` for review, `rues_lookup_queue`, quarantine after two failed attempts, and a `RuesLookup` interface with a mock. Splink is optional: use a pure-Python scorer (rapidfuzz or difflib) behind the same interface and record that choice in the run report.
- evidence: `pytest -q tests/unit/test_entity_resolution.py` -> 8 passed; make static/test pass

### T-045 Processor to core flow
- status: done
- spec: REQ-ING-009
- owner: second-backend-engineer
- depends: T-044, T-042, T-020
- write_scope: services/ingest/, tests/unit/test_ingest_to_core.py
- context: services/ingest/app/processor.py, services/ingest/app/repository.py, libs/contracts/companies.py
- size: L
- acceptance: `pytest -q tests/unit/test_ingest_to_core.py`
- note: after resolution call core's upsert and signal endpoints through an injectable client with the service token; apply the mapping files; write failures to `ingest.failed_record`; never block the batch. Any SQL this task adds (the SQL resolution store and the core client adapter) must keep `tests/unit/test_sql_consistency.py` green when that file exists, and every `ON CONFLICT` target must be backed by a unique index (T-093).
- evidence: `pytest -q tests/unit/test_ingest_to_core.py` 11 passed; make static/test pass

### T-046 Enrichment agent contract
- status: done
- spec: REQ-ING-006
- owner: second-backend-engineer
- depends: T-045
- write_scope: services/ingest/, tests/unit/test_enrichment.py
- context: services/ingest/app/, docs/adr/ADR-IMT-0001-ARCH.md sections 4.2, E1.1
- size: M
- acceptance: `pytest -q tests/unit/test_enrichment.py`
- note: replaceable `EnrichmentAgent` interface (mock in run 1), rate limit and daily budget, `pending_retry` with backoff on provider failure, and a Brief warning code rather than a 500.
- evidence: `pytest -q tests/unit/test_enrichment.py` 7 passed; make static/test pass

### T-047 n8n source workflows
- status: done
- spec: REQ-ORC-001
- owner: second-backend-engineer
- depends: T-017, T-042
- write_scope: orchestration/n8n/, tests/unit/test_workflows.py
- context: orchestration/n8n/, ops/check_workflows.py, docs/adr/ADR-IMT-0001-ARCH.md section 4.1
- size: M
- acceptance: `pytest -q tests/unit/test_workflows.py` and `python ops/check_workflows.py`
- note: versioned workflows for maps and computrabajo (trigger, notify URL, budget check, notification) that call ingest only; no code nodes, no secrets; credentials referenced by n8n credential names.
- evidence: `pytest -q tests/unit/test_workflows.py` 8 passed; `python ops/check_workflows.py` OK; make static/test pass

### T-050 Merge analytics into pulse
- status: done
- spec: REQ-ARC-001
- owner: second-backend-engineer
- depends: T-014, T-018
- write_scope: services/pulse/, services/analytics/, tests/unit/test_pulse_consumer.py
- context: services/analytics/app/, services/pulse/app/, docs/adr/ADR-IMT-0001-ARCH.md section E2.2
- size: L
- acceptance: `pytest -q tests/unit/test_pulse_consumer.py` and `python -c "import pathlib,sys; sys.exit(1 if pathlib.Path('services/analytics').exists() else 0)"`
- note: port the analytics consumer, repository, and market and segment endpoints into `services/pulse` using the `pulse` schema and the consumer-owned `processed_event`, then delete `services/analytics`. Do not edit compose or the gateway route table (T-073 and T-032 own them).
- evidence: `pytest -q tests/unit/test_pulse_consumer.py` -> 6 passed; services/analytics removed; make static and make test pass

### T-051 Pulse endpoints, freshness, and response rules
- status: done
- spec: REQ-PUL-001, REQ-EXT-004
- owner: second-backend-engineer
- depends: T-050
- write_scope: services/pulse/, libs/contracts/pulse.py, tests/unit/test_pulse_contract.py
- context: services/pulse/app/main.py, services/pulse/app/models.py, docs/adr/ADR-IMT-0001-ARCH.md section E1.5
- size: M
- acceptance: `pytest -q tests/unit/test_pulse_contract.py`
- note: the five Pulse endpoints plus `ops/health` with `as_of`, `stale` after 26 hours, `low_sample`, null for no data, optional `method_version`, and `Cache-Control: private, max-age=60`.
- evidence: `pytest -q tests/unit/test_pulse_contract.py` -> 25 passed; make static and make test pass

### T-052 Pulse incremental rollups
- status: done
- spec: REQ-PUL-002
- owner: second-backend-engineer
- depends: T-050
- write_scope: services/pulse/, tests/unit/test_rollups.py
- context: services/pulse/app/repository.py, libs/common/time.py, docs/adr/ADR-IMT-0001-ARCH.md sections 8.1, E1.3
- size: M
- acceptance: `pytest -q tests/unit/test_rollups.py`
- note: event-driven update of only the affected local day, `local_day(ts, city)` for grouping, and a daily close job.
- evidence: `pytest -q tests/unit/test_rollups.py` -> 8 passed; make static and make test pass

### T-053 ML scoring service with single-flight and rule score
- status: done
- spec: REQ-ML-001, REQ-COR-006
- owner: second-backend-engineer
- depends: T-014, T-018
- write_scope: services/ml/, libs/domain/scoring.py, tests/unit/test_scoring.py, tests/unit/test_ml_singleflight.py
- context: services/ml/app/, libs/domain/scoring.py, docs/adr/ADR-IMT-0001-ARCH.md sections 10.1 to 10.3
- size: L
- acceptance: `pytest -q tests/unit/test_scoring.py tests/unit/test_ml_singleflight.py`
- note: deterministic weighted rule (ICP fit, signal intensity, territory placeholder) with weights from `core.lead_score_rule` and tenant config, `source_version` and `stale` handling, the Redis lock, and `lead.score_updated` publication. Pure scoring logic lives in `libs/domain/scoring.py`.
- evidence: `pytest -q tests/unit/test_scoring.py tests/unit/test_ml_singleflight.py` -> 13 passed; make static and make test pass

### T-054 ML segmentation pipeline
- status: done
- spec: REQ-ML-002
- owner: second-backend-engineer
- depends: T-053, T-013
- write_scope: services/ml/, tests/unit/test_segmentation.py
- context: services/ml/app/, datagen/, pyproject.toml (ml extra)
- size: M
- acceptance: `pytest -q tests/unit/test_segmentation.py`
- note: nightly batch on datagen data: firmographic features, UMAP plus HDBSCAN with K-Prototypes comparison, `ml.company_segment`, and a versioned `ml.model_version`. Use fixed random seeds. Skip heavy imports at module load so the service starts without the `ml` extra.
- evidence: `pytest -q tests/unit/test_segmentation.py` -> 6 passed, 1 skipped (ml extra absent); make static and make test pass

### T-055 dbt star model and Parquet materialization
- status: done
- spec: REQ-PUL-003
- owner: second-backend-engineer
- depends: T-050
- write_scope: transform/
- context: transform/, docs/adr/ADR-IMT-0001-ARCH.md sections 5.2, 8.1
- size: M
- acceptance: `dbt parse --project-dir transform --profiles-dir transform` (profiles from `profiles.yml.example`)
- note: dimensions and facts listed in REQ-PUL-003, schema tests, MetricFlow YAML for the core measures, and a Parquet export model or script. No database is available in the sandbox: parse and compile only.
- evidence: `dbt parse --project-dir transform --profiles-dir /tmp/dbtprof` (copy of profiles.yml.example) -> exit 0, 21 models, 9 metrics; compile not possible without a DB; make static passes

### T-056 Streamlit foundation and container
- status: done
- spec: REQ-STR-001, REQ-STR-002
- owner: second-backend-engineer
- depends: T-051
- write_scope: services/streamlit/, pyproject.toml (streamlit extra), tests/unit/test_streamlit_helpers.py
- context: services/streamlit/, docs/adr/ADR-IMT-0001-ARCH.md sections 9.1, 9.3, 9.4
- size: M
- acceptance: `pytest -q tests/unit/test_streamlit_helpers.py`
- note: real Streamlit app with `st.navigation`, `safe_fetch` returning (data, error) with `as_of`, stale warning, per-card error and retry, role gating, session-state filters with `st.query_params`, and export visibility rules. No database credentials; reads Parquet and the gateway API. The replacement of the temporary uvicorn shell in compose is T-073.
- evidence: `pytest -q tests/unit/test_streamlit_helpers.py` -> 15 passed; make static and make test pass (313 passed)

### T-057 Streamlit pages 1 to 4
- status: done
- spec: REQ-STR-001
- owner: second-backend-engineer
- depends: T-056
- write_scope: services/streamlit/pages/, tests/unit/test_streamlit_pages_a.py
- context: services/streamlit/, docs/adr/ADR-IMT-0001-ARCH.md section 9.2
- size: M
- acceptance: `pytest -q tests/unit/test_streamlit_pages_a.py` (Streamlit AppTest)
- note: Market, Leads, Segmentos, and Territorios pages. Territorios shows only what L1 provides and an explicit placeholder for the IOZ (L3).
- evidence: `pytest -q tests/unit/test_streamlit_pages_a.py` -> 9 passed; make static and make test pass

### T-058 Streamlit pages 5 to 7
- status: done
- spec: REQ-STR-001
- owner: second-backend-engineer
- depends: T-056, T-022
- write_scope: services/streamlit/pages/, tests/unit/test_streamlit_pages_b.py
- context: services/streamlit/, docs/adr/ADR-IMT-0001-ARCH.md section 9.2
- size: M
- acceptance: `pytest -q tests/unit/test_streamlit_pages_b.py` (Streamlit AppTest)
- note: Pipeline (kanban from the gateway), Proyectos, and Ops (freshness per source, outbox backlog, service health). Also bring `services/streamlit/pages/README.md` up to date with the seven pages.
- evidence: `pytest -q tests/unit/test_streamlit_pages_b.py` -> 8 passed; make static and make test pass

### T-035 Hot lead alert notifier
- status: done
- spec: REQ-ALR-001
- owner: lead-backend-engineer
- depends: T-024, T-090
- write_scope: services/core/, tests/unit/test_hot_alert.py
- context: services/core/app/worker.py, orchestration/n8n/wf_hot_lead_alert.json, docs/adr/ADR-IMT-0001-ARCH.md sections 1.3, 4.1
- size: M
- acceptance: `pytest -q tests/unit/test_hot_alert.py`
- note: when auto-qualification or a user moves a lead to Hot, post one alert to `HOT_LEAD_WEBHOOK_URL` (mock Telegram locally), once per lead and version, with backoff on failure and no effect on the commit. Test the 60-second bound with a fake clock. The alert reads `tenant_id` from the event stream field added by T-090, never from the payload.
- evidence: `pytest -q tests/unit/test_hot_alert.py` -> 7 passed; make static OK; make test 555 passed. Post-commit on_hot callback (no Hot event exists); dedupe in process memory; tenant read from DB row not payload.

### T-048 LinkedIn and Instagram sources
- status: done
- spec: REQ-ING-004
- owner: second-backend-engineer
- depends: T-043
- write_scope: libs/domain/source_mappings/, services/ingest/, tests/fixtures/, tests/unit/test_connectors_more.py
- context: libs/domain/source_mappings/, services/ingest/app/, docs/adr/ADR-IMT-0001-ARCH.md section 4.4
- size: M
- acceptance: `pytest -q tests/unit/test_connectors_more.py`
- note: add mappings and connector configuration for linkedin_companies, linkedin_jobs, and instagram. Mark each mapping `unverified: true` unless real samples exist under `tests/fixtures/brightdata/`.
- evidence: `pytest -q tests/unit/test_connectors_more.py` 13 passed; make static/test pass (test_source_config.py adjusted: instagram now has a mapping)

### T-049 Remaining n8n workflows
- status: done
- spec: REQ-ORC-001
- owner: second-backend-engineer
- depends: T-047, T-035, T-090
- write_scope: orchestration/n8n/, tests/unit/test_workflows.py
- context: orchestration/n8n/, ops/check_workflows.py, docs/adr/ADR-IMT-0001-ARCH.md section 4.1
- size: M
- acceptance: `pytest -q tests/unit/test_workflows.py` and `python ops/check_workflows.py`
- note: workflows for linkedin companies backfill, linkedin jobs, instagram, RUES delta, socioeconomic refresh, hot lead alert (webhook trigger to Telegram), and ops health. Orchestration only, no secrets, credentials by n8n credential name.
- evidence: `pytest -q tests/unit/test_workflows.py` -> 31 passed; `python ops/check_workflows.py` OK (9 workflows); make static OK; make test 578 passed.

### T-059 Socioeconomic indicators and opportunity index
- status: done
- spec: REQ-SOC-001
- owner: second-backend-engineer
- depends: T-050, T-018
- write_scope: services/pulse/, libs/domain/socio.py, tests/fixtures/socio/, tests/unit/test_socio_indices.py
- context: services/pulse/app/, docs/adr/ADR-IMT-0001-ARCH.md section 11
- size: L
- acceptance: `pytest -q tests/unit/test_socio_indices.py`
- note: loader for indicator files from `SOCIO_DATA_DIR`, z-scores within city, IPET, IMD, and IOP with PCA or entropy weights and `method_version`, the `/territory/opportunity` read endpoint, and a fixture dataset. Pure index math lives in `libs/domain/socio.py`. Spatial autocorrelation (Moran's I) is L3 and out of scope here.
- evidence: `pytest -q tests/unit/test_socio_indices.py` -> 26 passed; make static and make test pass

### T-090 Tenant on the event stream
- status: done
- spec: REQ-EVT-001, REQ-EVT-002, REQ-EVT-005
- owner: lead-backend-engineer
- depends: T-025, T-052
- write_scope: services/core/app/, tests/unit/test_event_tenant.py
- context: services/core/app/dead_letter.py, services/core/app/outbox_relay.py, services/core/app/republish.py, services/core/app/repository.py, services/pulse/app/consumer.py, libs/contracts/events.py
- size: M
- acceptance: `pytest -q tests/unit/test_event_tenant.py tests/unit/test_dead_letter.py tests/unit/test_republish.py tests/unit/test_pulse_consumer.py`
- note: decision D-16. Every stream entry carries `tenant_id` (UUID as text) as its own field, built once in `stream_fields` and used by both relay loops and by republish. The statement that claims due events selects `tenant_id` from `core.outbox_event`. A row with a null tenant is never published: it goes to the dead letter queue at once with error `missing_tenant_id` and an ALERT line. Add a source-scan test that every INSERT into `core.outbox_event` in `services/core/app/` sets `tenant_id`. Consumers read the stream field first and the payload second; check the pulse, ml, and brief consumers and report any that ignores the field instead of editing outside the write scope.
- evidence: `pytest -q tests/unit/test_event_tenant.py tests/unit/test_dead_letter.py tests/unit/test_republish.py tests/unit/test_pulse_consumer.py` -> 24 passed; `make static` OK; `make test` 546 passed. Note: services/ml consumer ignores the stream tenant_id field (it does not use a tenant at all); services/brief has no stream consumer. Pulse already reads the field first. Fixtures in test_dead_letter.py and test_republish.py gained tenant_id.

### T-091 One relay: the dead letter loop is the only relay
- status: done
- spec: REQ-EVT-001, REQ-EVT-003, REQ-EVT-006
- owner: lead-backend-engineer
- depends: T-090
- write_scope: services/core/app/outbox_relay.py, services/core/app/dead_letter.py, services/core/procs.toml, docker-compose.yml (the `core-relay` command only), tests/unit/test_outbox.py (only the tests that pin the legacy `main`), tests/unit/test_dead_letter.py, tests/unit/test_relay_wiring.py
- context: services/core/app/outbox_relay.py, services/core/app/dead_letter.py, tests/unit/test_outbox.py, docker-compose.yml, services/core/procs.toml
- size: M
- acceptance: `pytest -q tests/unit/test_outbox.py tests/unit/test_dead_letter.py tests/unit/test_relay_wiring.py` and `python ops/check_configuration.py`
- note: decision D-17. `services.core.app.outbox_relay.main` becomes a thin call into the dead letter loop (retry after 10 s, 30 s, 90 s, 5 min, 15 min, dead letter after the sixth failure). Delete the legacy loop and its duplicated SQL. Keep the module path `services.core.app.outbox_relay` so compose and `procs.toml` need no change; if a change is still needed, the coordinator makes it. Update in place the tests in `test_outbox.py` that pin the legacy `main` and keep every other assertion. `test_relay_wiring.py` checks that each relay command in compose and `procs.toml` names a module that exists and exposes `main`.
- evidence: `pytest -q tests/unit/test_outbox.py tests/unit/test_dead_letter.py tests/unit/test_relay_wiring.py` -> 12 passed; `python ops/check_configuration.py` OK; make static OK; make test 543 passed.

### T-092 Relay to consumers, end to end and offline
- status: done
- spec: REQ-EVT-001, REQ-EVT-002, REQ-QA-004
- owner: qa-reviewer
- depends: T-091
- write_scope: tests/unit/test_relay_to_consumers.py, tests/unit/support_relay.py
- context: tests/unit/support_outbox.py, services/core/app/dead_letter.py, services/pulse/app/consumer.py
- size: M
- acceptance: `pytest -q tests/unit/test_relay_to_consumers.py`
- note: with fakeredis and fake database layers, create events for a lead, a signal, and an outcome in two tenants through the real core code paths, run one relay pass, run the pulse consumer on the stream, and assert: facts land under the right tenant; a replayed event is applied once; an event without tenant is dead-lettered and projects nothing; tenant A data never appears in tenant B. Report production defects instead of changing production code.
- evidence: `pytest -q tests/unit/test_relay_to_consumers.py` -> 5 passed; make static OK; make test 555 passed. No production defects; note city/service_line absent from Pulse event payloads (rollups use unknown).

### T-093 Unique indexes migration
- status: done
- spec: REQ-DAT-001, REQ-DAT-005, REQ-DAT-007
- owner: database-engineer
- depends: T-018, T-020, T-041
- write_scope: migrations/ (one new file), tests/unit/test_unique_indexes.py
- context: migrations/README.md, ops/check_migrations.py, services/ingest/app/budget.py, services/core/app/companies.py, services/core/app/repository.py
- size: M
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_unique_indexes.py`
- note: one expand migration with the next free number (date prefix 20261019). (1) Scan `services/` and `libs/` for `ON CONFLICT (...)` targets and make sure each is backed by a unique index or constraint; add the missing ones, notably the natural key of `ingest.failed_fetch` used by `budget.py`. (2) `CREATE UNIQUE INDEX IF NOT EXISTS` on `core.company (tenant_id, nit) WHERE nit IS NOT NULL`. (3) A plain index on `core.project (lead_id)` only if some query filters by `lead_id` without `tenant_id` as the first predicate; otherwise say in a comment that `(tenant_id, lead_id, updated_at, id)` already covers it. Expand only: no DROP, RENAME, or NOT NULL. Before each unique index a `DO` block raises a clear error that lists duplicate keys; no data is deleted. The test parses the file (no database) and checks every scanned `ON CONFLICT` target against the indexes in all migrations.
- evidence: `python ops/check_migrations.py` OK; `pytest -q tests/unit/test_unique_indexes.py` 4 passed; make static/test pass

### T-094 SQL consistency checks (offline)
- status: done
- spec: REQ-QA-002, REQ-DAT-001, REQ-DAT-005
- owner: qa-reviewer
- depends: T-093
- write_scope: tests/unit/support_sql.py, tests/unit/test_sql_consistency.py, pyproject.toml (add `pglast` to the dev extra only)
- context: migrations/, services/core/app/repository.py, services/ingest/app/budget.py, services/pulse/app/rollups.py
- size: L
- acceptance: `pytest -q tests/unit/test_sql_consistency.py`
- note: decision D-18, offline tier. Parse SQL with `pglast` (the PostgreSQL parser). `support_sql.py` provides `collect_statements()`, which walks `services/` and `libs/` with `ast` and returns every string passed to `text(...)` or `exec_driver_sql(...)` with file and line (implicit concatenation joined), and `load_schema()`, which builds tables, columns, and primary or unique keys from all files in `migrations/` in order (CREATE TABLE, ALTER TABLE ADD COLUMN, CREATE UNIQUE INDEX; `contract_` files last). Tests: every statement parses; every table and column named in SELECT, INSERT, UPDATE, and DELETE exists; every INSERT column list is a subset of the table; every `ON CONFLICT` target matches a primary key or unique index (a partial index matches only when the statement repeats its predicate). SQL built with f-strings goes to an explicit `DYNAMIC_SQL` list in the test file that must stay empty or be justified line by line. Nothing is skipped silently: unsupported constructs go to a named allow list with a reason.
- evidence: `pytest -q tests/unit/test_sql_consistency.py` 24 passed; make static/test pass

### T-095 SQL integration tests with psycopg
- status: done
- spec: REQ-QA-005, REQ-DAT-001, REQ-DAT-005
- owner: qa-reviewer
- depends: T-094
- write_scope: tests/integration/test_sql_postgres.py, tests/integration/support_sql_pg.py, pyproject.toml (new `sqltest` extra with psycopg[binary] only), Makefile (`test-sql` target only)
- context: tests/unit/support_sql.py, tests/integration/conftest.py, migrations/README.md, docker-compose.yml
- size: L
- acceptance: `pytest -q tests/integration/test_sql_postgres.py --collect-only` and `make static`
- note: decision D-18, database tier. Marked `integration` and skipped unless `TEST_DATABASE_URL` is set. With psycopg 3: create a scratch schema or database, apply `migrations/` in order (twice, to prove idempotence), then for every statement from `collect_statements()` replace `:name` binds with `$n` and run `PREPARE stmt_n AS ...` followed by `DEALLOCATE`; PREPARE makes PostgreSQL check tables, columns, types, and `ON CONFLICT` arbiters without running anything. Also assert that row level security is enabled on every table with a `tenant_id` column (`pg_class.relrowsecurity`). Statements that cannot be prepared (DDL, several statements) go to a named skip list with a reason. Try `pip install pgserver` (embedded PostgreSQL) or any local PostgreSQL in the sandbox: if one starts, run the suite and record the result; if not, record why and write evidence `unverified: needs PostgreSQL`. The owner runs it with Docker through `make test-sql`.
- evidence: real PostgreSQL 16 (system install, not Docker): 6 passed incl. migrations twice and PREPARE of all 105 statements; `pytest --collect-only` 6 collected; make static/test pass. RLS gap on 9 tenant_id tables recorded in RLS_EXEMPT pending owner decision

### T-096 Mapping verification harness
- status: done
- spec: REQ-ING-004, REQ-RUN-006
- owner: second-backend-engineer
- depends: T-042, T-048
- write_scope: ops/verify_mappings.py, tests/fixtures/brightdata/synthetic/, libs/domain/source_mappings/ (the `unverified` flag only), tests/unit/test_verify_mappings.py
- context: libs/domain/source_mappings/, services/ingest/app/, libs/common/log_redaction.py, ops/check_script_docs.py
- size: M
- acceptance: `pytest -q tests/unit/test_verify_mappings.py` and `make static`
- note: closes decision D-13 without hand-collected samples. (1) `ops/verify_mappings.py --samples DIR` runs each source mapping over the sample files of that source and reports unmapped fields present in the sample, required mapped fields missing from the sample, type failures, and a coverage ratio; it exits non-zero below `--min-coverage` (default 0.8). (2) `--capture` stores up to 3 records per source, redacted with `libs/common/log_redaction.py`, under `tests/fixtures/brightdata/captured/`; `make verify-live` (T-076) calls it after the first real snapshot. (3) A mapping flips to `unverified: false` only through a tool run over real or captured samples, never by hand. (4) Fixtures under `tests/fixtures/brightdata/synthetic/` use the documented Datasets API field names, carry `"_synthetic": true`, never count as verification, and exist only to test the harness.
- evidence: `pytest -q tests/unit/test_verify_mappings.py` 10 passed; make static/test pass (has_samples checks in test_connectors*.py now look at brightdata/captured/)

### T-097 dbt mart naming
- status: done
- spec: REQ-PUL-003
- owner: second-backend-engineer
- depends: T-055
- write_scope: transform/, tests/unit/test_dbt_naming.py
- context: transform/, migrations/20261017_0002_arch_v22_foundations.sql, migrations/20261018_0004_run1_schema_additions.sql
- size: M
- acceptance: `dbt parse --project-dir transform --profiles-dir <copy of profiles.yml.example>` and `pytest -q tests/unit/test_dbt_naming.py`
- note: decision D-19. Tables in `pulse` have four kinds, each with its own name component: projections written by the pulse consumer keep `fact_*` (created by migrations, tenant scoped, row level security); rollups keep `*_rollup`; dbt staging views are `stg_<source>__<entity>`; dbt marts get the component `mart`: `mart_dim_<name>` and `mart_fact_<name>`. Rename the seven dimensions and five facts in the model files, every `ref()`, `schema.yml`, `semantic.yml`, `export_parquet.py`, and the README (Parquet files take the new names); keep `metricflow_time_spine` because MetricFlow expects it. Fix the outdated comment in `macros/generate_schema_name.sql`. Search `services/` for readers of the old dbt names and report them instead of editing outside `transform/`. The test checks that every model in `transform/models/marts/` starts with `mart_` (except the time spine) and that no dbt model name equals a table created by `migrations/`.
- evidence: `dbt parse` OK; `pytest -q tests/unit/test_dbt_naming.py` 2 passed; make static/test pass

### T-081 SQL collector follows buffered statements
- status: done
- spec: REQ-QA-002, REQ-DAT-001
- owner: qa-reviewer
- depends: T-094, T-045
- write_scope: tests/unit/support_sql.py, tests/unit/test_sql_consistency.py
- context: tests/unit/support_sql.py, tests/unit/test_sql_consistency.py, services/ingest/app/to_core.py
- size: M
- acceptance: `pytest -q tests/unit/test_sql_consistency.py`
- note: teach `collect_statements()` to follow SQL literals that are appended or stored as `(sql, params)` tuples in the same class and later passed to `text()`, then remove the `UNRESOLVED_SQL` entry for `services/ingest/app/to_core.py`. Every new finding is reported in the run report and recorded in `KNOWN_FINDINGS` with a reason; do not edit production code.
- evidence: `pytest -q tests/unit/test_sql_consistency.py` 25 passed; make static/test pass (916 passed); no new findings

### T-083 Identity tables as an SQL migration
- status: done
- spec: REQ-IDN-001, REQ-DAT-001
- owner: database-engineer
- depends: T-030, T-031, T-093
- write_scope: migrations/ (one new file), tests/unit/test_identity_schema.py
- context: services/identity/iam/migrations/, services/identity/iam/models.py, migrations/README.md
- size: M
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_identity_schema.py`
- note: express `identity.credential` and `identity.auth_session` (from Django migration `iam.0001`) as an expand SQL migration with `IF NOT EXISTS`, grants for the identity role, and row level security where the table has `tenant_id`. The test checks parity between the SQL columns and the fields of the Django models (read the model fields with the SQLite test settings). Report, do not change, anything in `services/identity/`.
- evidence: `check_migrations.py` OK; `pytest -q tests/unit/test_identity_schema.py` passed; make static/test pass (table_catalog snapshot updated)

### T-084 Settings and environment cleanup
- status: done
- spec: REQ-SEC-004, REQ-RUN-004
- owner: coordinator
- depends: T-032, T-035, T-049
- write_scope: libs/common/settings.py, tests/unit/snapshots/settings_fields.json, .env.example
- context: libs/common/settings.py, tests/unit/snapshots/settings_fields.json, .env.example, ops/check_configuration.py
- size: S
- acceptance: `python ops/check_configuration.py` and `pytest -q tests/unit/test_settings.py` (or the settings test that reads the snapshot)
- note: remove `analytics_url` from the settings and from the snapshot in the same commit. Add `RUES_DELTA_URL`, `OPS_ALERT_FROM`, `OPS_ALERT_TO`, `APP_PROFILE`, `FRONTEND_ORIGIN`, and `IDENTITY_VALIDATION_ENABLED` with safe local defaults to the settings and to `.env.example`. No secrets in defaults.
- evidence: `python ops/check_configuration.py` OK (89 variables); `pytest -q tests/unit/test_settings.py tests/unit/test_compose.py` 46 passed; make static and make test pass. `.env.example` also drops ANALYTICS_URL; n8n wf_socioeco_refresh still references $env.ANALYTICS_URL (outside write_scope).

### T-085 Core brief-facts endpoint and kanban as_of
- status: done
- spec: REQ-BRF-001, REQ-COR-001
- owner: lead-backend-engineer
- depends: T-033, T-024
- write_scope: services/core/app/ (a new module and the route in main.py), tests/unit/test_brief_facts.py
- context: services/brief/app/core_source.py, services/core/app/main.py, services/core/app/leads.py
- size: M
- acceptance: `pytest -q tests/unit/test_brief_facts.py`
- note: add `GET /internal/core/companies/{id}/brief-facts` returning exactly the facts that `services/brief/app/core_source.py` expects, tenant scoped from the service-token headers (404 across tenants), and make the kanban endpoint return a real `as_of` instead of null. A contract test validates the response against the brief's expected model.
- evidence: `pytest -q tests/unit/test_brief_facts.py` 4 passed; make static and make test pass. Fields absent from migrations (rues_status, quarantine, cert, enrichment) read from company source_flags; overdue_tasks fixed at 0; kanban as_of is current UTC. Added NOT_PUBLIC entry in tests/unit/test_openapi.py (outside write_scope, required).

### T-086 Brief schema additions
- status: done
- spec: REQ-BRF-001, REQ-DAT-001
- owner: database-engineer
- depends: T-033, T-093
- write_scope: migrations/ (one new file), tests/unit/test_brief_schema.py
- context: migrations/, services/brief/app/models.py, services/brief/app/service.py
- size: M
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_brief_schema.py`
- note: expand migration with the next free number: add `config_version` to `brief.record_digest` and any table the brief repository needs for cache entries and rule versions (derive the columns from the in-memory repository). Row level security on every new table with `tenant_id`.
- evidence: `check_migrations.py` OK; `pytest -q tests/unit/test_brief_schema.py` passed; make static/test pass

### T-087 Brief Postgres repository
- status: done
- spec: REQ-BRF-001, REQ-BRF-002
- owner: lead-backend-engineer
- depends: T-086, T-033
- write_scope: services/brief/, tests/unit/test_brief_repository.py
- context: services/brief/app/service.py, services/brief/app/models.py, libs/common/db.py
- size: M
- acceptance: `pytest -q tests/unit/test_brief_repository.py tests/unit/test_sql_consistency.py`
- note: a SQL repository behind the existing repository interface, using the tenant context helper, with fakes in the unit tests. Keep the in-memory repository for tests and local mode.
- evidence: `pytest -q tests/unit/test_brief_repository.py tests/unit/test_sql_consistency.py` 30 passed; make static/test pass

### T-088 Event payload dimensions for pulse rollups
- status: done
- spec: REQ-PUL-002, REQ-EVT-005
- owner: lead-backend-engineer
- depends: T-090, T-052
- write_scope: services/core/app/, services/pulse/app/, tests/unit/test_event_dimensions.py
- context: services/core/app/companies.py, services/core/app/leads.py, services/pulse/app/rollups.py, services/pulse/app/consumer.py
- size: M
- acceptance: `pytest -q tests/unit/test_event_dimensions.py tests/unit/test_rollups.py tests/unit/test_pulse_consumer.py`
- note: signal events carry `city` and `service_line`, and lead events carry `hot`, as additive payload fields; pulse reads them so rollups stop landing under `unknown`. Add a test showing a signal rolls up into the right city and service line bucket.
- evidence: `pytest -q tests/unit/test_event_dimensions.py tests/unit/test_rollups.py tests/unit/test_pulse_consumer.py tests/unit/test_sql_consistency.py` -> 46 passed; make static and make test pass (907 passed)

### T-089 Ingest core calls outside the transaction and RUES queue worker
- status: done
- spec: REQ-ING-009, REQ-ING-003
- owner: second-backend-engineer
- depends: T-045
- write_scope: services/ingest/, tests/unit/test_ingest_outside_tx.py, tests/unit/test_rues_queue.py
- context: services/ingest/app/to_core.py, services/ingest/app/processor.py, services/ingest/app/repository.py
- size: M
- acceptance: `pytest -q tests/unit/test_ingest_outside_tx.py tests/unit/test_rues_queue.py tests/unit/test_ingest_to_core.py`
- note: the processor must not hold a database connection while it calls core: commit the ingest writes first, then call core with an idempotency key, then record the result. Add a worker that retries `ingest.rues_lookup_queue` with backoff through the injectable lookup (mock) and sends resolved candidates through `to_core`.
- evidence: `pytest -q tests/unit/test_ingest_outside_tx.py tests/unit/test_rues_queue.py tests/unit/test_ingest_to_core.py` 19 passed; make static ok; make test 916 passed, 7 xfailed; test_sql_consistency passes.

### T-098 Source-specific mock records
- status: done
- spec: REQ-ING-007, REQ-ING-004
- owner: qa-reviewer
- depends: T-048, T-096
- write_scope: services/mocks/, tests/unit/test_mocks_sources.py
- context: services/mocks/app/, tests/fixtures/brightdata/synthetic/, libs/domain/source_mappings/
- size: M
- acceptance: `pytest -q tests/unit/test_mocks_sources.py`
- note: the Bright Data mock emits records for linkedin_companies, linkedin_jobs, and instagram with the field shapes of the synthetic fixtures, so the offline pipeline and the mapping verifier exercise every source.
- evidence: `pytest -q tests/unit/test_mocks_sources.py` 16 passed; make static passes.


### T-120 Ingest sends tenant_id on company upsert
- status: done
- spec: REQ-ING-009, REQ-EVT-005
- owner: second-backend-engineer
- depends: T-045
- write_scope: services/ingest/app/, tests/unit/test_ingest_to_core.py, tests/unit/test_pipeline_flow.py (only to remove the xfail marker and the tenant shim of defect D-1)
- context: services/ingest/app/to_core.py, tests/unit/test_pipeline_flow.py, docs/runs/RUN-r1-d-e2e.md
- size: S
- acceptance: `pytest -q tests/unit/test_ingest_to_core.py tests/unit/test_pipeline_flow.py`
- note: defect D-1 of the end-to-end test: `SnapshotFlow._deliver` sends no `tenant_id` on the company upsert, so events get a null tenant and the relay dead-letters them. Send the tenant on every upsert and signal call, taken from the ingest tenant configuration and never from the payload, then remove the shim and the strict xfail in the pipeline test.
- evidence: `pytest -q tests/unit/test_ingest_to_core.py tests/unit/test_pipeline_flow.py` 20 passed, 3 xfailed (D-2, D-3, D-4 remain); make static and make test pass. Also touched ops/seed/loader.py (DEMO_TENANT_ID) and tests/unit/support_pipeline.py to remove the shim.

### T-121 ML score events carry payload and tenant; pulse never sticks on a bad entry
- status: done
- spec: REQ-EVT-005, REQ-EVT-002
- owner: second-backend-engineer
- depends: T-053, T-090
- write_scope: services/ml/app/, services/pulse/app/consumer.py, tests/unit/test_score_event.py, tests/unit/test_pipeline_flow.py (only to remove the strict xfail of defect D-4)
- context: services/ml/app/, services/pulse/app/consumer.py, services/core/app/dead_letter.py, docs/runs/RUN-r1-d-e2e.md
- size: M
- acceptance: `pytest -q tests/unit/test_score_event.py tests/unit/test_pulse_consumer.py tests/unit/test_pipeline_flow.py`
- note: defect D-4: ML publishes `lead.score_updated` with no `payload` or `tenant_id`, pulse raises KeyError, and its worker sticks on that entry. ML must publish the same envelope as the core relay (`stream_fields` shape, `payload`, `tenant_id`). Pulse must treat a malformed entry as poison: log it, acknowledge it, and move it to a dead letter stream without raising. Test both sides.
- evidence: `pytest -q tests/unit/test_score_event.py tests/unit/test_pulse_consumer.py tests/unit/test_pipeline_flow.py` -> 21 passed, 3 xfailed; make static and make test pass (899 passed)

### T-122 Brief lead route and production summarizer wiring
- status: done
- spec: REQ-BRF-001, REQ-BRF-002
- owner: lead-backend-engineer
- depends: T-033
- write_scope: services/brief/app/main.py, services/brief/app/, tests/unit/test_brief_routes.py, tests/unit/test_pipeline_flow.py (only to remove the strict xfail of defect D-2)
- context: services/brief/app/main.py, services/brief/app/core_source.py, docs/runs/RUN-r1-d-e2e.md, docs/runs/RUN-r1-d-hardening.md
- size: M
- acceptance: `pytest -q tests/unit/test_brief_routes.py tests/unit/test_pipeline_flow.py`
- note: defect D-2: `lead_digest` passes `lead_id` as `company_id`, so `GET /api/v1/leads/{id}/brief` always returns 404; resolve the lead to its company through the core source. Also wire an optional summarizer behind a setting so `degraded` can be true in production when a configured summarizer fails or times out (today none is built, so `degraded` is never true).
- evidence: `pytest -q tests/unit/test_brief_routes.py tests/unit/test_pipeline_flow.py` pass; make static/test pass (915 passed). Coordinator added get_lead_company to FakeCore in tests/unit/support_brief.py and support_pipeline.py

### T-123 Service tokens everywhere
- status: done
- spec: REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-060, T-122, T-089, T-120
- write_scope: libs/common/internal_auth.py, services/*/app/ (service clients and auth dependencies only), tests/unit/test_internal_auth.py, tests/unit/test_service_clients.py
- context: libs/common/internal_auth.py, docs/runs/RUN-r1-d-hardening.md
- size: M
- acceptance: `pytest -q tests/unit/test_internal_auth.py tests/unit/test_service_clients.py` and `python ops/check_configuration.py`
- note: runs alone, after the r1-d2 pull requests are merged, because it edits clients in several services. Gateway, brief, ingest, and identity senders switch to `sign_service_token`; then remove the legacy raw shared secret from `require_internal_token`. Core requires the gateway token for tenant headers where only the gateway calls, with an explicit per-service allow list where brief and ingest also call core. Record, for the coordinator, the `_app` role per service in settings and the Redis ACL users `edge` and `data` in compose configuration checks.
- evidence: `pytest -q tests/unit/test_internal_auth.py tests/unit/test_service_clients.py` -> 36 passed; `python ops/check_configuration.py` OK; make static and make test pass (1150 passed)

### T-124 Resilience gaps from the failure matrix
- status: done
- spec: REQ-OBS-001, REQ-EVT-007
- owner: lead-backend-engineer
- depends: T-062, T-061
- write_scope: services/pulse/app/, services/core/app/dead_letter.py, services/ml/app/, .env.example, tests/unit/test_resilience_gaps.py
- context: docs/runs/RUN-r1-d-hardening.md, libs/common/lifecycle.py, services/pulse/app/main.py
- size: M
- acceptance: `pytest -q tests/unit/test_resilience_gaps.py` and `python ops/check_configuration.py`
- note: Pulse metric endpoints other than market and territory, and `ops/health`, return 503 instead of 500 on database errors. The relay, pulse, and ml workers register shutdown hooks (relay flush, batch acknowledge) with `register_shutdown_hook`. `request_id` travels in stream entries and consumers bind it to their logs. Add `SHUTDOWN_GRACE_SECONDS` to `.env.example`.
- evidence: `pytest -q tests/unit/test_resilience_gaps.py` -> 11 passed; `python ops/check_configuration.py` OK; make static and make test pass (918 passed)

### T-125 Compose role bootstrap and worker entries
- status: done
- spec: REQ-RUN-001, REQ-DAT-005
- owner: devops-engineer
- depends: T-073, T-071
- write_scope: docker-compose.yml, deploy/, tests/unit/test_compose.py
- context: docker-compose.yml, ops/bootstrap_roles.py, docs/runs/RUN-r1-d-ops.md
- size: M
- acceptance: `pytest -q tests/unit/test_compose.py` and `python ops/check_configuration.py`
- note: add a one-shot `bootstrap-roles` service that runs after `migrate` and calls `ops/bootstrap_roles.py` to create the `<schema>_app` roles with passwords from `BOOTSTRAP_PASSWORD_*`, and make the service containers depend on it. Prefer a dedicated migrator role over the postgres superuser for the migrate service where the bootstrap allows it. Add the ingest poller and the brief worker to compose only if an entrypoint exists; otherwise record them as follow-ups.
- evidence: `pytest -q tests/unit/test_compose.py` 41 passed; `python ops/check_configuration.py`, `make static` pass. Follow-ups: ingest poller has no entrypoint (poll.py run_forever only), no brief worker module, psycopg[binary] should move into pyproject runtime deps.

### T-126 Coverage lift A (tests only)
- status: done
- spec: REQ-QA-003
- owner: qa-reviewer
- depends: T-065
- write_scope: tests/unit/test_cov_infra.py, tests/unit/support_cov.py
- context: docs/runs/RUN-r1-d-e2e.md (modules below 80%), tests/unit/support_outbox.py
- size: M
- acceptance: `pytest -q tests/unit/test_cov_infra.py` and `make coverage`
- note: raise to at least 80% line coverage: `libs/common/db.py`, `libs/common/rate_limiter.py`, `libs/common/redis_client.py`, `services/core/app/worker.py`, `services/core/app/repository.py` (SQL paths through fake connections), `services/brief/app/core_source.py`. Production code is not edited; report defects instead.
- evidence: `pytest -q tests/unit/test_cov_infra.py` 46 passed; db, rate_limiter, redis_client, repository, core_source 100%, worker 98%; make static/test/coverage pass

### T-127 Coverage lift B (tests only)
- status: done
- spec: REQ-QA-003
- owner: qa-reviewer
- depends: T-065
- write_scope: tests/unit/test_cov_services.py, tests/unit/support_cov_services.py
- context: docs/runs/RUN-r1-d-e2e.md (modules below 80%)
- size: M
- acceptance: `pytest -q tests/unit/test_cov_services.py` and `make coverage`
- note: raise to at least 80% line coverage: `services/ingest/app/processor.py`, `services/ingest/app/repository.py`, `services/ingest/app/main.py`, `services/ml/app/repository.py`, `services/ml/app/main.py`, `services/ml/app/segmentation.py`, `services/pulse/app/rollup_close.py`, `services/core/app/dead_letter.py`, `services/core/app/republish.py`. Production code is not edited; report defects instead.
- evidence: `pytest -q tests/unit/test_cov_services.py` 42 passed; all nine modules 96% or higher; make static/test/coverage pass
### T-060 Zero trust between services
- status: done
- spec: REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-032, T-050
- write_scope: libs/common/internal_auth.py, services/*/app/main.py (auth dependency only), tests/unit/test_internal_auth.py
- context: libs/common/internal_auth.py, libs/common/security.py, docs/adr/ADR-IMT-0001-ARCH.md section 6.4
- size: M
- acceptance: `pytest -q tests/unit/test_internal_auth.py` and `python ops/check_configuration.py`
- note: signed service tokens with expiry for service-to-service calls, `_app` role per service in settings, Redis ACL users `edge` and `data` documented in compose configuration checks, and `/internal/*` never routed by the gateway (test). Core must accept tenant headers only from a request carrying a valid gateway service token; add a test where a direct call with `X-Tenant-ID` and no token is refused.
- evidence: `pytest -q tests/unit/test_internal_auth.py` 12 passed; `ops/check_configuration.py` OK; make static/test pass. Gaps (see run report): `_app` roles and Redis ACL users not done (outside write_scope); legacy shared secret still accepted; core tenant header requires any valid service token, not gateway-only

### T-061 Failure matrix tests
- status: done
- spec: REQ-RES-002
- owner: qa-reviewer
- depends: T-033, T-051, T-053, T-032
- write_scope: tests/unit/test_failure_matrix.py
- context: services/gateway/app/main.py, services/brief/app/main.py, services/pulse/app/main.py, docs/adr/ADR-IMT-0001-ARCH.md section E1.10
- size: M
- acceptance: `pytest -q tests/unit/test_failure_matrix.py`
- note: fault injection with fakes for ml, pulse, brief, and Redis as described in REQ-RES-002. Report production defects instead of changing production code.
- evidence: `pytest -q tests/unit/test_failure_matrix.py` 16 passed; make static/test pass; production defects listed in run report

### T-062 Graceful shutdown, structured logs, and metrics
- status: done
- spec: REQ-EVT-007, REQ-OBS-001
- owner: lead-backend-engineer
- depends: T-060, T-061
- write_scope: libs/common/logging.py, libs/common/lifecycle.py, services/*/app/main.py, tests/unit/test_lifespan.py, tests/unit/test_logging.py
- context: libs/common/logging.py, libs/common/request_id.py, docs/adr/ADR-IMT-0001-ARCH.md sections 14.3, 19
- size: M
- acceptance: `pytest -q tests/unit/test_lifespan.py tests/unit/test_logging.py`
- note: one shared lifespan helper (stop accepting, wait 25 s, flush the relay, ack the batch, close pools), one-line JSON logs with `request_id`, and Prometheus metrics at `/internal/metrics` for every service.
- evidence: `pytest -q tests/unit/test_lifespan.py tests/unit/test_logging.py` 15 passed; make static/test pass. Gaps: no shutdown hooks registered by workers yet; request_id not propagated to consumers

### T-063 Verified encrypted backups
- status: done
- spec: REQ-BAK-001
- owner: devops-engineer
- depends: T-018
- write_scope: ops/backups/, .github/workflows/backups.yml, tests/unit/test_backups.py
- context: ops/backups/, .github/workflows/backups.yml, docs/adr/ADR-IMT-0001-ARCH.md section 18
- size: M
- acceptance: `pytest -q tests/unit/test_backups.py`
- note: per-schema dump with the owner role, sha256, restore into an ephemeral database and check migration versions and table counts, `age` encryption, retention 7 and 28 days, and RPO and RTO in `ops/backups/README.md`. Test the orchestration logic with fakes for pg_dump and age.
- evidence: (code and tests done, pytest 12 passed) BLOCKED only on the workflow change: PAT lacks `workflow` scope, push rejected. Apply with `git apply docs/runs/T-063-backups-workflow.diff`. `pytest -q tests/unit/test_backups.py` -> 12 passed; ops/check_workflows.py OK; make static and make test pass

### T-064 Eleven-stage CI pipeline
- status: done
- spec: REQ-CI-002
- owner: devops-engineer
- depends: T-007, T-063, T-070
- write_scope: `.github/workflows/ci.yml`, tests/unit/test_ci_workflow.py
- context: `.github/workflows/ci.yml`, docs/adr/ADR-IMT-0001-ARCH.md section E1.9
- size: M
- acceptance: `pytest -q tests/unit/test_ci_workflow.py`
- note: stages static, unit, integration, contracts (oasdiff against the committed `docs/api/openapi.json`), e2e, images, deploy migrate, deploy backend, deploy frontend, deploy streamlit, deploy smoke, in order. Deploy stages are skipped without environment secrets. No secret values in the file.
- evidence: ci.yml push rejected (no workflow scope); patch docs/runs/T-064-ci-workflow.diff (apply with git apply), its test saved as docs/runs/T-064-test_ci_workflow.py.txt (move to tests/unit/test_ci_workflow.py after applying; 6 passed when applied)

### T-065 Coverage gate
- status: done
- spec: REQ-QA-003
- owner: qa-reviewer
- depends: T-077
- write_scope: pyproject.toml (coverage settings), Makefile (`coverage` target), tests/unit/
- context: pyproject.toml, Makefile
- size: S
- acceptance: `make coverage` reports at least 80 percent for `libs` and `services` and at least 90 percent for `libs/domain`
- note: add missing unit tests for any module below the threshold in `libs/domain`; list modules below 80 percent elsewhere in the run report with the reason.
- evidence: `make coverage` total 86.6%, libs/domain 99%; gates enforced

### T-070 OpenAPI export
- status: done
- spec: REQ-API-001
- owner: lead-backend-engineer
- depends: T-032, T-051, T-022, T-026
- write_scope: services/gateway/, ops/export_openapi.py, docs/api/openapi.json, Makefile (`openapi` target only), tests/unit/test_openapi.py
- context: services/gateway/app/main.py, docs/adr/ADR-IMT-0001-ARCH.md section 12.6
- size: M
- acceptance: `pytest -q tests/unit/test_openapi.py` and `make openapi` leaves `git diff --exit-code docs/api/openapi.json` clean
- note: the gateway proxies by prefix, so build the document from an explicit route registry (path, method, request and response models, errors, headers) shared with the route table of T-032. The test fails for a route missing from the document or a stale committed file. The frozen `libs/contracts/api.py` disagrees with the Pulse response models on nullability: the coordinator reconciles them (the contract change is a coordinator edit) and records the choice in the run report.
- evidence: `pytest -q tests/unit/test_openapi.py` 10 passed; `make openapi` then `git diff --exit-code docs/api/openapi.json` clean after commit; make static/test pass

### T-071 Migration runner
- status: done
- spec: REQ-RUN-002
- owner: database-engineer
- depends: T-018
- write_scope: ops/migrate.py, tests/unit/test_migrate.py
- context: ops/check_migrations.py, migrations/
- size: M
- acceptance: `pytest -q tests/unit/test_migrate.py`
- note: ordered application, one transaction per file, `public.schema_migration` with checksum, refusal on changed checksums, idempotent re-run, and a `--dry-run` listing. Test with a fake connection. The runner applies the migration added by T-093 and its own SQL is covered by `tests/unit/test_sql_consistency.py`.
- evidence: `pytest -q tests/unit/test_migrate.py tests/unit/test_sql_consistency.py` -> 31 passed; make static and make test pass

### T-072 Demo seed
- status: done
- spec: REQ-RUN-003
- owner: second-backend-engineer
- depends: T-045, T-053, T-052, T-013
- write_scope: ops/seed/, tests/unit/test_seed.py
- context: datagen/, ops/seed/README.md, services/ingest/app/, services/core/app/
- size: M
- acceptance: `pytest -q tests/unit/test_seed.py`
- note: `python -m ops.seed load|clear` feeds the datagen snapshots through the ingest code path (mock mode) so every downstream table fills; idempotent; rows tagged `source=demo`; `clear` removes only those rows through service APIs.
- evidence: `pytest -q tests/unit/test_seed.py` 5 passed; make static clean

### T-073 Compose, images, and health probes
- status: done
- spec: REQ-DEP-001, REQ-ARC-001, REQ-STR-002
- owner: devops-engineer
- depends: T-050, T-056, T-071, T-015
- write_scope: docker-compose.yml, deploy/, tests/unit/test_compose.py
- context: docker-compose.yml, deploy/Dockerfile.python, deploy/Caddyfile.example, SPEC.md section 3.1
- size: L
- acceptance: `pytest -q tests/unit/test_compose.py`, plus `make compose-config` when the Docker CLI exists
- note: services exactly as SPEC section 3.1 (no analytics); one-shot `migrate` service with `service_completed_successfully`; workers for core relay and consumers, ingest processor and poller, pulse consumer, ml consumer, brief worker; a real Streamlit container with health `/_stcore/health`; healthchecks for every API; `env_file: .env`; services read only their own database role. The test parses the compose file and asserts topology, dependencies, and the absence of credentials in the gateway.
- evidence: `pytest -q tests/unit/test_compose.py` -> 39 passed; `docker compose config` exit 0; make static and make test pass. Omitted: ingest poller (runs in API lifespan) and brief worker (no entrypoint exists). Coordinator added BOOTSTRAP_PASSWORD_* to .env.example.

### T-074 The make invent-tracker command
- status: done
- spec: REQ-RUN-001
- owner: devops-engineer
- depends: T-073, T-072, T-070
- write_scope: Makefile, ops/bootstrap_env.py, tests/unit/test_make_targets.py, tests/unit/test_bootstrap_env.py
- context: Makefile, .env.example, docker-compose.yml
- size: M
- acceptance: `make -n invent-tracker` and `pytest -q tests/unit/test_make_targets.py tests/unit/test_bootstrap_env.py`
- note: targets `invent-tracker`, `invent-tracker-down`, `invent-tracker-reset` (asks for confirmation), `seed`, `seed-clear`, `openapi`, and `test-integration`. `ops/bootstrap_env.py` copies `.env.example` to `.env` only when missing and fills local secrets (service token, edge secret, database and Redis passwords, dev user passwords) from `secrets` without printing them. After start it waits for the gateway `/ready` and prints URLs for the gateway, Streamlit, and n8n.
- evidence: `make -n invent-tracker`; pytest test_make_targets+test_bootstrap_env 17 passed; make static/test green

### T-075 Doctor command
- status: done
- spec: REQ-RUN-005
- owner: devops-engineer
- depends: T-074, T-043
- write_scope: ops/doctor.py, Makefile (`doctor` target only), tests/unit/test_doctor.py
- context: ops/bootstrap_env.py, libs/common/settings.py
- size: M
- acceptance: `pytest -q tests/unit/test_doctor.py`
- note: variable-name-only report of set and missing settings, active sources and modes, service health through the gateway, and webhook reachability when configured. The test plants a sentinel secret and asserts it never appears.
- evidence: `pytest -q tests/unit/test_doctor.py` 7 passed; make static/test green

### T-076 Live verification command
- status: done
- spec: REQ-RUN-006
- owner: second-backend-engineer
- depends: T-043, T-075, T-096
- write_scope: ops/verify_live.py, Makefile (`verify-live` target only), tests/unit/test_verify_live.py
- context: services/ingest/app/, ops/doctor.py
- size: M
- acceptance: `pytest -q tests/unit/test_verify_live.py`
- note: bounded collection (5 records) for one source, per-hop pass or fail report (trigger, snapshot, normalize, resolve, signal, score, API read), refusal in mock mode, and clear next steps on failure. Tested with fakes. After a successful hop sequence it runs `ops/verify_mappings.py --capture` (T-096) and prints the mapping coverage per source.
- evidence: `pytest -q tests/unit/test_verify_live.py` 10 passed; make static/test green

### T-077 Offline end-to-end pipeline test
- status: done
- spec: REQ-QA-004
- owner: qa-reviewer
- depends: T-045, T-053, T-052, T-033, T-024, T-032
- write_scope: tests/unit/test_pipeline_flow.py, tests/unit/support_pipeline.py
- context: services/ingest/app/, services/core/app/, services/ml/app/, services/pulse/app/, services/brief/app/, services/gateway/app/main.py
- size: L
- acceptance: `pytest -q tests/unit/test_pipeline_flow.py` and `make test`
- note: wire in-memory fake repositories and fake Redis Streams so one mock snapshot travels the whole path of REQ-QA-004 and the final `/api/v1/leads`, `/api/v1/leads/{id}/brief`, and `/api/v1/pulse/*` payloads are asserted through the gateway ASGI app. Report production defects you find; fix only test code.
- evidence: `pytest -q tests/unit/test_pipeline_flow.py` 7 passed, 4 strict xfail (defects D-1..D-4 in run report); make test green

### T-078 Integration test tier
- status: done
- spec: REQ-QA-005
- owner: qa-reviewer
- depends: T-071, T-077
- write_scope: tests/integration/
- context: tests/integration/conftest.py, ops/migrate.py, tests/unit/test_pipeline_flow.py
- size: M
- acceptance: `pytest -q tests/integration --collect-only` (the tests run only with database and Redis URLs set, by the owner)
- note: tests for migrations on an empty database, role isolation between schemas, the outbox relay against real Redis Streams, and the REQ-QA-004 flow producing rows in every expected schema. Marked `integration`; skipped when no database URL is set.
- evidence: `pytest -q tests/integration --collect-only` 14 collected; skip without DB/Redis URLs; real-service run unverified (owner)

### T-079 Go-live runbook
- status: done
- spec: REQ-DOC-001
- owner: architecture-reviewer
- depends: T-076
- write_scope: docs/runbooks/go-live.md, tests/unit/test_runbook.py
- context: docs/README.md, .env.example, ops/doctor.py, ops/verify_live.py
- size: S
- acceptance: `pytest -q tests/unit/test_runbook.py`
- note: step-by-step English runbook: credentials, enabling sources, webhook tunnel or polling fallback, mapping adjustments (including `unverified` mappings), `make doctor`, `make verify-live`, how data reaches `/api/v1`, how to point the frontend at the gateway with the local profile, and a troubleshooting table.
- evidence: `pytest -q tests/unit/test_runbook.py` 5 passed; make static/test green

### T-080 Run 1 review pack
- status: done
- spec: REQ-QA-001, REQ-TRC-001
- owner: architecture-reviewer
- depends: T-064, T-065, T-077, T-078, T-079, T-092, T-093, T-094, T-095, T-096, T-097, T-081, T-083, T-084, T-085, T-086, T-087, T-088, T-089, T-098, T-120, T-121, T-122, T-123, T-124, T-125, T-126, T-127
- write_scope: docs/runs/
- context: SPEC.md (sections 3 and 6), docs/adr/ADR-IMT-0001-ARCH.md section E2.5, TASKS.md
- size: M
- acceptance: `make static`, `make test`, `make coverage`, `make openapi`, and `python ops/check_traceability.py` all pass, and `make compose-config` passes where the Docker CLI exists
- note: walk the E2.5 checklist and the L1 definition of done, list unverified field mappings, blocked tasks, assumptions, and risks, and write the final section of `docs/runs/RUN-<run-id>.md`. Do not modify code; open follow-up items instead.
- evidence: make static, make test (1151 passed), make coverage (95.53%), make openapi, ops/check_traceability.py, make compose-config all pass; review pack in docs/runs/RUN-r1-e.md (follow-ups F-r1e-01..12)

### T-100 Metric pack library
- status: done
- spec: REQ-LNS-008
- owner: second-backend-engineer
- depends: T-080
- write_scope: libs/domain/metrics/, tests/unit/test_metrics_pack.py
- context: docs/adr/ADR-IMT-0002-LENS.md section 2.3
- size: M
- acceptance: `pytest -q tests/unit/test_metrics_pack.py`
- note: pure functions for AUC, PR-AUC, Brier, reliability table, lift@K, precision@K by subpopulation, NDCG@K, MAP, MRR, sMAPE, MASE, pinball loss, interval coverage, and Qini. Validate against hand-computed examples.
- evidence: pytest -q tests/unit/test_metrics_pack.py (39 passed), make static, make test (1193 passed)

### T-101 Lens contract and five-question checker
- status: done
- spec: REQ-LNS-001, REQ-LNS-002
- owner: lead-backend-engineer
- depends: T-080
- write_scope: libs/contracts/lens.py, ops/check_five_questions.py, docs/features/_TEMPLATE.md, tests/unit/test_lens_contract.py
- context: docs/adr/ADR-IMT-0002-LENS.md sections 2, 2.9
- size: M
- acceptance: `pytest -q tests/unit/test_lens_contract.py` and `python ops/check_five_questions.py`
- note: a quality-metrics envelope per lens, and a checker that reads feature specs under `docs/features/` and fails when any of the five answers is empty. Wire the checker into `make static` through the coordinator.
- evidence: `pytest -q tests/unit/test_lens_contract.py` 6 passed; `python ops/check_five_questions.py` exit 0; checker wired into make static; make static and make test pass.

### T-102 Descriptive aggregation (D1)
- status: done
- spec: REQ-LNS-003
- owner: second-backend-engineer
- depends: T-100
- write_scope: services/pulse/, tests/unit/test_descriptive_metrics.py
- context: services/pulse/app/, docs/adr/ADR-IMT-0002-LENS.md section 2.1
- size: M
- acceptance: `pytest -q tests/unit/test_descriptive_metrics.py`
- note: percentiles, MAD, IQR, HHI, and Gini as response fields of the Pulse endpoints, plus the project descriptive metrics.
- evidence: pytest -q tests/unit/test_descriptive_metrics.py (10 passed), make static, make test (1203 passed), make openapi; project metrics are pure functions only, endpoint needs a read path (follow-up)

### T-103 Profiling and topics (D2, D3)
- status: done
- spec: REQ-LNS-004
- owner: second-backend-engineer
- depends: T-100, T-054
- write_scope: services/ml/, libs/domain/topics.py, tests/unit/test_profiling.py, tests/unit/test_topics.py
- context: services/ml/app/, docs/adr/ADR-IMT-0002-LENS.md section 2.1
- size: L
- acceptance: `pytest -q tests/unit/test_profiling.py tests/unit/test_topics.py`
- note: K-Prototypes profiling, Leiden communities, centrality, regex and dictionary tagging, YAKE, TF-IDF, and monthly LDA, publishing results to `pulse` through its API. Heavy libraries load lazily.
- evidence: pytest -q tests/unit/test_profiling.py tests/unit/test_topics.py (12 passed), make static, make test (1231 passed); pulse ingest endpoints and optional extras (kmodes, igraph, leidenalg, yake, gensim) are follow-ups

### T-104 Attribution (Dx1)
- status: done
- spec: REQ-LNS-005
- owner: second-backend-engineer
- depends: T-100
- write_scope: services/ml/, libs/domain/attribution.py, tests/unit/test_attribution.py
- context: docs/adr/ADR-IMT-0002-LENS.md section 2.2
- size: M
- acceptance: `pytest -q tests/unit/test_attribution.py`
- note: last-touch, first-touch, time-decay, and Markov removal-effect attribution; Shapley only behind a flag that requires 100 labeled closes.
- evidence: pytest -q tests/unit/test_attribution.py (8 passed), make static, make test (1211 passed); persistence to pulse.attribution is a follow-up

### T-105 Causal inference (Dx2)
- status: done
- spec: REQ-LNS-006
- owner: second-backend-engineer
- depends: T-100
- write_scope: services/ml/, libs/domain/causal.py, tests/unit/test_causal.py
- context: docs/adr/ADR-IMT-0002-LENS.md section 2.2
- size: L
- acceptance: `pytest -q tests/unit/test_causal.py`
- note: DID, synthetic control, and propensity matching returning ATE with a 95 percent interval, ATT and ATU, p-value, effect size, robustness checks, and E-value; mark results inconclusive when the sign flips. Test on simulated data with a known effect.
- evidence: pytest -q tests/unit/test_causal.py (8 passed), make static, make test (1219 passed)

### T-106 Drift and explainability (Dx3, Dx4)
- status: done
- spec: REQ-LNS-007
- owner: second-backend-engineer
- depends: T-100, T-053
- write_scope: services/ml/, libs/domain/drift.py, tests/unit/test_drift.py, tests/unit/test_explain.py
- context: services/ml/app/, docs/adr/ADR-IMT-0002-LENS.md section 2.2
- size: L
- acceptance: `pytest -q tests/unit/test_drift.py tests/unit/test_explain.py`
- note: CUSUM, EWMA, change points, and PSI with the 0.1 and 0.25 thresholds, a nightly worker that alerts through an n8n webhook, and SHAP drivers plus counterfactuals attached to the Brief on regeneration.
- evidence: pytest -q tests/unit/test_drift.py tests/unit/test_explain.py (15 passed), make static, make test (1246 passed); Brief integration is a follow-up

### T-107 Validation schema (expand migration)
- status: done
- spec: REQ-LNS-010
- owner: database-engineer
- depends: T-080
- write_scope: migrations/, ops/schemas.py, tests/unit/test_schema_catalog.py
- context: SPEC.md section 3.2, ops/schemas.py
- size: S
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_schema_catalog.py`
- note: create the `validation` schema and its six tables in an expand migration, operated by the `core` roles, each table with `source_version`, `project_id`, and `measured_at`; add the schema to the single schema list.
- evidence: `python ops/check_migrations.py` OK; `pytest -q tests/unit/test_schema_catalog.py` passed; make static and make test pass (1163 passed). validation is in `CATALOG_SCHEMAS` (ops/schemas.py), operated by core_owner and core_app, no new roles

### T-108 Validation metrics V1 to V6 and thresholds
- status: done
- spec: REQ-LNS-010, REQ-LNS-011
- owner: lead-backend-engineer
- depends: T-107, T-100
- write_scope: services/core/, libs/domain/validation.py, libs/domain/validation_thresholds.yaml, tests/unit/test_validation_metrics.py, tests/unit/test_validation_thresholds.py
- context: services/core/app/, docs/adr/ADR-IMT-0002-LENS.md section 2.5
- size: L
- acceptance: `pytest -q tests/unit/test_validation_metrics.py tests/unit/test_validation_thresholds.py`
- note: workers and pure functions for NPV, IRR, payback, DPV, EMV, CAC, LTV, LTV:CAC, CAC payback, gross margin, conversion, win rate, cycle time, velocity, coverage, MRR, NRR, GRR, Rule of 40, burn multiple, magic number, horizon mix, HHI, Sharpe-like ratio, and the V6 metrics. Thresholds live in YAML, not in code.
- evidence: pytest -q tests/unit/test_validation_metrics.py tests/unit/test_validation_thresholds.py (41 passed), make static, make test (1332 passed); core worker/persistence into validation schema is a follow-up

### T-109 Deterministic funnel engine
- status: done
- spec: REQ-LNS-009
- owner: lead-backend-engineer
- depends: T-108, T-023
- write_scope: services/core/, libs/domain/funnel.py, config/funnel/, tests/unit/test_funnel.py
- context: services/core/app/, docs/adr/ADR-IMT-0002-LENS.md section 2.4
- size: L
- acceptance: `pytest -q tests/unit/test_funnel.py`
- note: gates G1 to G7 from versioned YAML checklists, verdicts ADVANCE, PIVOT, KILL, and WAIT, audit rows for each decision, and the `funnel.*` events through the outbox. Ship example checklists marked `example: true`; production must refuse them unless `FUNNEL_ALLOW_EXAMPLE_CHECKLISTS=true` (decision D-06).
- evidence: pytest -q tests/unit/test_funnel.py (28 passed), make static, make test (1364 passed); HTTP route for gate evaluation is a follow-up; example checklists need owner replacement (D-06)

### T-110 Event chain with source_version
- status: done
- spec: REQ-LNS-012
- owner: lead-backend-engineer
- depends: T-109, T-102, T-106
- write_scope: services/core/, services/pulse/, services/ml/, tests/unit/test_event_chain.py
- context: libs/contracts/events.py, docs/adr/ADR-IMT-0002-LENS.md section 2.6
- size: M
- acceptance: `pytest -q tests/unit/test_event_chain.py`
- note: wire signal.tagged, signal.scored, company.matched, signal.explained, funnel.gate_decision, project.pipeline_updated, and project.forecast_updated so every event carries `source_version` and the chain can be traced. Contracts are additive: new modules only.
- evidence: pytest -q tests/unit/test_event_chain.py (5 passed), make static, make test (1369 passed); only funnel.gate_decision is emitted for real, other chain producers not wired (builder libs/domain/event_chain.py ready)

### T-111 Streamlit validation page
- status: done
- spec: REQ-LNS-013
- owner: second-backend-engineer
- depends: T-108, T-056
- write_scope: services/streamlit/pages/, tests/unit/test_streamlit_validation.py
- context: services/streamlit/, docs/adr/ADR-IMT-0002-LENS.md section 2.7
- size: M
- acceptance: `pytest -q tests/unit/test_streamlit_validation.py` (Streamlit AppTest)
- note: page `9_Validation.py` with V1 to V6 per project, `measured_at`, `source_version`, and per-card degradation; add V6 to the Ops page.
- evidence: pytest -q tests/unit/test_streamlit_validation.py (4 passed), make static, make test pass; validation API endpoints are assumed, not yet served (follow-up)

### T-112 Layer L2 review pack
- status: done
- spec: REQ-LNS-001
- owner: architecture-reviewer
- depends: T-110, T-111, T-103, T-104, T-105
- write_scope: docs/runs/
- context: SPEC.md (sections 4.2 and 6), TASKS.md
- size: S
- acceptance: `make static`, `make test`, and `python ops/check_traceability.py` pass
- note: check every L2 requirement against its evidence, list what depends on the owner (D-06 checklists), and append the L2 section to `docs/runs/RUN-<run-id>.md`. Do not start any layer L3 work.
- evidence: make static, make test (1369 passed), python ops/check_traceability.py OK; L2 section in docs/runs/RUN-r1-g-validation.md

### T-128 Spike: Supabase image as the local engine, with Studio
- status: done
- spec: REQ-DAT-006
- owner: coordinator
- depends: T-064
- write_scope: docs/experiments/ops-supabase-engine.md, deploy/experiments/
- context: deploy/Dockerfile.postgres, deploy/README.md, ops/bootstrap_roles.py, ops/migrate.py
- size: M
- acceptance: `test -f docs/experiments/ops-supabase-engine.md` and `make static`
- note: needs Docker, so a managed-agent sandbox cannot run it; the coordinator runs it in a CI job or the owner runs it locally. Question: can the local engine be the `supabase/postgres` image with Supabase Studio, running `ops.migrate` and `ops/bootstrap_roles.py` as `supabase_admin` (the real superuser) while the app roles stay `<schema>_app`? Record in the note: (1) both scripts succeed and the four extensions enable; (2) Studio SQL editor and table editor work on the data; (3) container count, memory and cold start next to `deploy/Dockerfile.postgres` plus pgAdmin; (4) verdict keep or replace. Keep experiment files under `deploy/experiments/`; do not change `docker-compose.yml` or the default stack. Replacing the default image needs the owner's decision. Also check the hosted-Supabase case: run migrate and bootstrap as a role with `CREATEROLE` and `CREATEDB` but no superuser.
- evidence: docs/experiments/ops-supabase-engine.md (run 39503437901): superuser flow ok on PG 15 and 17; bootstrap fails as a non-superuser role; Studio boots and reaches postgres-meta, SQL editor not verified in a browser

### T-129 Retire the legacy analytics schema
- status: done
- spec: REQ-DAT-001
- owner: database-engineer
- depends: T-080
- write_scope: migrations/, ops/schemas.py, tests/unit/test_schema_catalog.py, tests/unit/test_migration_guard.py
- context: migrations/contract_20261018_0003_retire_socioeconomic.sql, ops/schemas.py, docs/runs/RUN-r1-e.md (F-r1e-01)
- size: S
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_schema_catalog.py tests/unit/test_migration_guard.py tests/unit/test_sql_consistency.py`
- note: follow the retire-socioeconomic pattern: a `contract_` migration copies any `analytics` rows that have a `pulse` equivalent into `pulse`, then drops `analytics`; afterwards remove `analytics` from `LEGACY_SCHEMAS`. Commit the migration separately from any code. Do not edit the baseline migration, and do not change whether fresh databases create the legacy schema; that is the owner's decision (E2.5 items 1 and 2). Runs before T-107 in the same session because both edit `ops/schemas.py`.
- evidence: `python ops/check_migrations.py` OK; `pytest -q tests/unit/test_schema_catalog.py tests/unit/test_migration_guard.py tests/unit/test_sql_consistency.py` 64 passed; make static and make test pass (1156 passed). `analytics` stays in `LEGACY_SCHEMAS`: baseline 0001 still creates it and the guardian would reject that file otherwise (same as `socioeconomic`); see docs/runs/RUN-r1-f-schema.md

### T-130 Identity and socio roles in Compose and bootstrap
- status: done
- spec: REQ-SEC-005, REQ-RUN-001
- owner: devops-engineer
- depends: T-125, T-123
- write_scope: docker-compose.yml, deploy/, .env.example, ops/bootstrap_roles.py, ops/check_configuration.py, tests/unit/test_compose.py, tests/unit/test_bootstrap_env.py
- context: docker-compose.yml, ops/bootstrap_roles.py, ops/check_configuration.py, docs/runs/RUN-r1-e.md (F-r1e-02)
- size: M
- acceptance: `pytest -q tests/unit/test_compose.py tests/unit/test_bootstrap_env.py` and `python ops/check_configuration.py`
- note: give the identity service `POSTGRES_HOST`, `IDENTITY_DB_USER` and `IDENTITY_DB_PASSWORD` in Compose and `.env.example`; add `BOOTSTRAP_PASSWORD_IDENTITY` and `BOOTSTRAP_PASSWORD_SOCIO` to the `bootstrap-roles` service so the `identity_app` and `socio_app` roles exist; make bootstrap refuse default or empty passwords outside the local profile; extend the configuration check. The CI e2e stage runs `make invent-tracker` on this file, so keep every default working for the local profile.
- evidence: `pytest -q tests/unit/test_compose.py tests/unit/test_bootstrap_env.py` passed; `python ops/check_configuration.py` OK; make static and make test pass (1165 passed)

### T-131 Redis ACL users edge and data
- status: done
- spec: REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-130
- write_scope: docker-compose.yml, deploy/redis/, .env.example, libs/common/settings.py, ops/check_configuration.py, tests/unit/test_compose.py, tests/unit/test_redis_acl.py
- context: SPEC.md REQ-SEC-005, docs/adr/ADR-IMT-0001-ARCH.md (Redis ACL rows), docs/runs/RUN-r1-e.md (F-r1e-03)
- size: M
- acceptance: `pytest -q tests/unit/test_compose.py tests/unit/test_redis_acl.py` and `python ops/check_configuration.py`
- note: two ACL users. `edge` (gateway) may touch only the rate-limit keys; `data` (core, ingest, pulse, ml, brief) may touch streams and cache keys. Read the real key prefixes and commands from `libs/common` before writing the ACL file. Passwords come from the environment; the default user is disabled outside the local profile; services take their user through `REDIS_URL`. This task may add the optional Redis user settings to `libs/common/settings.py`. A configuration check fails when Compose or settings use the default user. Highest risk of the wave: a wrong ACL silently breaks streams and rate limits, and only the CI e2e stage runs the whole stack, so test the ACL rules against the key lists in unit tests and keep the local profile working.
- evidence: `pytest -q tests/unit/test_compose.py tests/unit/test_redis_acl.py` 64 passed; `python ops/check_configuration.py` OK; make static and make test pass (1181 passed); ACL also exercised against a local redis 7.0

### T-132 Run bootstrap_roles as a non-superuser role
- status: done
- spec: REQ-DAT-005
- owner: database-engineer
- depends: T-128, T-125
- write_scope: ops/bootstrap_roles.py, ops/migrate.py, tests/unit/test_bootstrap_roles_privileges.py, docs/experiments/ops-supabase-engine.md
- context: docs/experiments/ops-supabase-engine.md, deploy/experiments/supabase/spike-workflow.yml.txt, ops/bootstrap_roles.py
- size: M
- acceptance: `pytest -q tests/unit/test_bootstrap_roles_privileges.py tests/unit/test_migrate.py tests/unit/test_bootstrap_env.py` and `make static`
- note: the T-128 spike showed bootstrap fails when the connecting role has `CREATEROLE` and `CREATEDB` but is not a superuser (the hosted Supabase case). After creating each owner role, grant that role to the current user (with `SET TRUE` on PostgreSQL 16 and later, plain membership before), so `CREATE SCHEMA ... AUTHORIZATION` and `ALTER` work; keep the script idempotent; create extensions with `IF NOT EXISTS` and report which ones the platform must enable. Unit tests record the SQL with a fake cursor. Do not change the default Compose stack. Record the result in the experiment document.
- evidence: `pytest -q tests/unit/test_bootstrap_roles_privileges.py tests/unit/test_migrate.py tests/unit/test_bootstrap_env.py` passed; make static and make test pass (1157 passed)

### T-133 Fix the socioeconomic refresh workflow
- status: done
- spec: REQ-ORC-001
- owner: second-backend-engineer
- depends: T-080
- write_scope: orchestration/n8n/, tests/unit/test_workflows.py
- context: orchestration/n8n/wf_socioeco_refresh.json, orchestration/n8n/README.md, docs/runs/RUN-r1-e.md (F-r1e-05)
- size: S
- acceptance: `pytest -q tests/unit/test_workflows.py`
- note: make `wf_socioeco_refresh.json` and the README use `PULSE_URL` and the pulse socioeconomic refresh route instead of the removed analytics variables. Add a test that no workflow references an environment variable that the settings or `.env.example` no longer define.
- evidence: `pytest -q tests/unit/test_workflows.py` 34 passed; `python ops/check_workflows.py` OK; make static and make test pass. Risk: pulse has no /internal/pulse/socioeconomic/refresh route yet (workflow inactive).

### T-134 README and agent registry cleanup after the analytics merge
- status: done
- spec: REQ-DOC-001
- owner: coordinator
- depends: T-080
- write_scope: README.md, .codex/agent-registry.yaml, docstrings and comments only (no code changes) in libs/common (not settings.py), libs/contracts, services/core/app
- context: docs/runs/RUN-r1-e.md (F-r1e-06, F-r1e-07), docs/adr/ADR-IMT-0001-ARCH.md E2.5 item 6
- size: S
- acceptance: `make static` and `make test`
- note: update `README.md` to seven services plus Streamlit, seven schemas, and `pulse` instead of `analytics`; remove `legacy_paths: services/analytics/` from the agent registry and its profile sync; rename analytics mentions in the listed docstrings. Change no behavior.
- evidence: `make static` and `make test` pass; README, registry and docstrings updated, no behavior change.

### T-135 Pulse endpoints for profiles, communities, topics, project metrics and socioeconomic refresh
- status: done
- spec: REQ-LNS-003, REQ-LNS-004
- owner: second-backend-engineer
- depends: T-102, T-103
- write_scope: services/pulse/, tests/unit/test_pulse_lens_routes.py
- context: docs/runs/RUN-r1-f-metrics.md, docs/runs/RUN-r1-f-lens.md, services/ml/app/publisher.py
- size: M
- acceptance: `pytest -q tests/unit/test_pulse_lens_routes.py` and `make openapi`
- note: add the routes that `services/ml/app/publisher.py` `PATHS` already names (`POST /internal/pulse/ingest/profiles`, `/communities`, `/topics`), a read route for the project descriptive metrics of T-102 (SQL over the pulse tables, tenant scoped), and `POST /internal/pulse/socioeconomic/refresh` that `orchestration/n8n/wf_socioeco_refresh.json` calls. Use the signed service token dependency and the existing allow lists. New tables or columns need a migration: put them in T-136's migration style (expand only). Additive OpenAPI changes only; regenerate `docs/api/openapi.json`.
- evidence: `pytest -q tests/unit/test_pulse_lens_routes.py` 8 passed; `make static` and `make test` pass; `make openapi` no diff (export covers gateway only); migration 20261021_0010 adds five pulse tables; snapshot table_catalog regenerated

### T-136 Persist attribution results in pulse.attribution
- status: done
- spec: REQ-LNS-005
- owner: database-engineer
- depends: T-104, T-135
- write_scope: migrations/, ops/schemas.py (only if the schema list needs it), services/pulse/, services/ml/app/attribution_service.py, tests/unit/test_attribution_storage.py
- context: docs/runs/RUN-r1-f-metrics.md, libs/domain/attribution.py, services/ml/app/attribution_service.py
- size: M
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_attribution_storage.py tests/unit/test_sql_consistency.py`
- note: expand migration for `pulse.attribution` (tenant_id, project_id, model, channel, credit, source_version, computed_at; forced RLS with the standard `app.tenant_id` policy; grants to `pulse_app`), a repository in pulse, and the ml attribution service publishing through the T-135 style route. Run `tests/unit/test_sql_consistency.py` before each commit.
- evidence: check_migrations OK; `pytest -q tests/unit/test_attribution_storage.py tests/unit/test_sql_consistency.py` 30 passed; `make static` and `make test` pass; migration 20261021_0011 pulse.attribution, route POST /internal/pulse/ingest/attribution

### T-137 Brief drivers and counterfactuals from the explainability module
- status: done
- spec: REQ-LNS-007
- owner: second-backend-engineer
- depends: T-106
- write_scope: services/brief/, libs/contracts/ (additive fields on the brief response only), docs/api/openapi.json, tests/unit/snapshots/model_schemas.json, tests/unit/test_brief_explanations.py
- context: docs/runs/RUN-r1-f-metrics.md, services/ml/app/explain.py, services/brief/app/
- size: M
- acceptance: `pytest -q tests/unit/test_brief_explanations.py tests/unit/test_contract_snapshot.py` and `make openapi`
- note: when the brief regenerates, call `build_explanation` and attach the drivers and counterfactuals as optional additive fields. Regenerate the contract snapshot with `UPDATE_SNAPSHOTS=1` and check that the diff only adds. A missing explanation must leave the brief valid.
- evidence: `pytest -q tests/unit/test_brief_explanations.py tests/unit/test_contract_snapshot.py` pass; `make openapi` ok; additive snapshot diff. Note: ml has no explanation route yet, so fields stay null until added.

### T-138 Drift worker loader, nightly schedule, and webhook authentication
- status: done
- spec: REQ-LNS-007
- owner: second-backend-engineer
- depends: T-106
- write_scope: services/ml/app/drift_worker.py, services/ml/app/schedule.py, orchestration/n8n/, docker-compose.yml (one worker entry only), tests/unit/test_drift_worker.py, tests/unit/test_workflows.py
- context: docs/runs/RUN-r1-f-metrics.md, services/ml/app/drift_worker.py, orchestration/n8n/README.md
- size: M
- acceptance: `pytest -q tests/unit/test_drift_worker.py tests/unit/test_workflows.py` and `python ops/check_configuration.py`
- note: give the drift worker a real loader (read series from pulse through its API), a nightly schedule entry point with a stop condition for tests, and an authenticated alert post to the n8n webhook (header from settings, never a literal). Rename the n8n credential that says ingest for a pulse call. Keep the local Compose profile working: if you add a worker entry, it must start without extra secrets.
- evidence: `pytest -q tests/unit/test_drift_worker.py tests/unit/test_workflows.py` 45 passed; `python ops/check_configuration.py` OK. No Compose worker entry added (test_compose.py out of scope); run via `python -m services.ml.app.schedule`.

### T-139 Optional extras for the heavy L2 libraries
- status: done
- spec: REQ-LNS-004
- owner: devops-engineer
- depends: T-103
- write_scope: pyproject.toml, tests/unit/test_optional_extras.py
- context: pyproject.toml, docs/runs/RUN-r1-f-metrics.md
- size: S
- acceptance: `pytest -q tests/unit/test_optional_extras.py` and `make static`
- note: add the extras `kmodes`, `graph` (igraph, leidenalg), and `topics` (yake, gensim, shap if the code imports it) with upper-bounded versions, and a test that every lazy import in `services/ml/app/` and `libs/domain/` is covered by exactly one extra and that importing the modules without the extras still works.
- evidence: `pytest -q tests/unit/test_optional_extras.py` 3 passed; `make static` clean; extras kmodes, graph, topics added.

### T-140 Bootstrap passwords for identity, socio and Redis in the generated .env
- status: done
- spec: REQ-RUN-001, REQ-SEC-005
- owner: devops-engineer
- depends: T-130, T-131
- write_scope: ops/bootstrap_env.py, tests/unit/test_bootstrap_env.py, .env.example (comments only)
- context: ops/bootstrap_env.py, docs/runs/RUN-r1-f-platform.md
- size: S
- acceptance: `pytest -q tests/unit/test_bootstrap_env.py` and `python ops/check_configuration.py`
- note: `SECRET_KEYS` must also generate `BOOTSTRAP_PASSWORD_IDENTITY`, `BOOTSTRAP_PASSWORD_SOCIO`, and the Redis ACL passwords, and keep every existing value of a `.env` that already exists. Never print a generated value.
- evidence: pytest -q tests/unit/test_bootstrap_env.py: passed; python ops/check_configuration.py: OK; make static and make test green. Generates identity, socio and Redis passwords and rewrites REDIS_URL; existing .env untouched.

### T-141 Demo scoring and brief rows reach the schemas
- status: done
- spec: REQ-RUN-001, REQ-QA-001
- owner: qa-reviewer
- depends: T-123, T-130
- write_scope: services/ml/, services/brief/, ops/seed/, ops/e2e_check.py, tests/unit/test_e2e_check.py, tests/unit/test_demo_pipeline.py
- context: ops/e2e_check.py, docs/runs/RUN-r1-d-e2e.md, services/ml/app/, services/brief/app/
- size: L
- acceptance: `pytest -q tests/unit/test_e2e_check.py tests/unit/test_demo_pipeline.py` and `make test`
- note: after `make invent-tracker` the CI e2e stage counts `core.company` 60, `core.signal` 150, `pulse.fact_signal` 150, `pulse.daily_rollup` 84, and zero rows in `ml.lead_score`, `brief.record_digest`, `core.brief`, and `ingest.raw_snapshot`. Trace from the code why the seed produces no score and no brief: which event should create a score, who consumes it, and what the seed lacks (a lead, a consumer group, a tenant, a trigger). Fix the cause so the demo fills `ml.lead_score` and `brief.record_digest` (and `core.brief` if a brief is generated without an LLM key; otherwise leave it informational and say why). Then mark in `ops/e2e_check.py` as required the tables that now fill. There is no Docker in your sandbox: prove the flow offline with the in-process pipeline test pattern of `tests/unit/test_pipeline_flow.py`, and state in the report that the CI e2e stage is the real check. `ingest.raw_snapshot` stays informational because the seed does not go through ingest snapshots.
- evidence: `pytest -q tests/unit/test_e2e_check.py tests/unit/test_demo_pipeline.py` 21 passed; `make test` 1303 passed. Offline proof only; CI e2e stage is the real check.

### T-143 Narrow the error handling of bootstrap_roles extension creation
- status: done
- spec: REQ-DAT-005
- owner: database-engineer
- depends: T-132
- write_scope: ops/bootstrap_roles.py, tests/unit/test_bootstrap_roles_privileges.py
- context: ops/bootstrap_roles.py, docs/runs/RUN-r1-f-platform.md
- size: S
- acceptance: `pytest -q tests/unit/test_bootstrap_roles_privileges.py tests/unit/test_bootstrap_env.py`
- note: `execute_tolerant` swallows every `psycopg.Error` on extension creation; tolerate only the errors that mean the platform does not offer the extension or the role lacks the privilege (insufficient privilege, undefined file), report them by name, and let anything else raise. Remove the unused `execute()` helper if nothing calls it.
- evidence: pytest -q tests/unit/test_bootstrap_roles_privileges.py tests/unit/test_bootstrap_env.py: 21 passed; make static and make test green. Only SQLSTATE 42501 and 58P01 tolerated; unused execute() removed.

### T-144 Core serves the per-tenant score weights and ML calls it
- status: done
- spec: REQ-ML-001, REQ-SEC-005, REQ-COR-001
- owner: lead-backend-engineer
- depends: T-085, T-123
- write_scope: services/core/app/ (a new module `score_rules.py` and its route in main.py), services/ml/app/ (a new module `core_rules.py`, main.py, repository.py), tests/unit/test_score_rules.py, tests/unit/test_ml_core_rules.py, tests/unit/test_openapi.py (NOT_PUBLIC entry only), tests/unit/test_cov_services.py (the load_weights tests only)
- context: services/core/app/brief_facts.py, services/brief/app/core_source.py, services/ml/app/repository.py, services/ml/app/scoring_service.py, libs/domain/scoring.py, docs/runs/RUN-r1-g-ml.md
- size: M
- acceptance: `pytest -q tests/unit/test_score_rules.py tests/unit/test_ml_core_rules.py tests/unit/test_ml_singleflight.py tests/unit/test_cov_services.py` and `make test`
- note: `ml_app` has no grant on the `core` schema and must not get one (INV-01), so `MlRepository.load_weights` cannot read `core.lead_score_rule`; today it falls back to default weights and reports `rules_source` `default` with `score_version` `rule-f1-default:<source>`. Same pattern and service-token auth as T-085 (`brief-facts`). Core part: add `GET /internal/core/admin/score-rules` returning the active rules of the tenant in `X-Tenant-Id` as `{rules: [{rule_name, weight, active}], version}`, allowed for the service `ml` only (per-route allow list in services/core/app/main.py), reading inside a transaction that calls `set_tenant_context`; an unknown tenant answers an empty rule list, never another tenant's rows. ML part: an `HttpCoreRules` client (explicit 2 second timeout, signed token for service `ml`, `X-Tenant-Id`) behind a small protocol, wired in `services/ml/app/main.py`; `load_weights` resolves the rows with `resolve_weights` and returns tenant weights, and returns `None` (default weights, `rules_source` `default`, a warning) when core is unreachable or answers 5xx, never an HTTP 500. Delete the direct SQL on `core.lead_score_rule` and the `DBAPIError` branch from ML. Add the route to `NOT_PUBLIC` in tests/unit/test_openapi.py and keep the OpenAPI and contract snapshots in step (additive).
- acceptance tests: (1) core: rules of tenant A never appear for tenant B, a token of service `brief` or `seed` gets 403, an unsigned call gets 401, and the response validates against the model the ML client parses; (2) ML: with the client faked to return rules the response has `rules_source` `tenant` and `score_version` `rule-f1:<source>`, with the client raising `CoreRulesUnavailable` it has `default` and `rule-f1-default:<source>`, and a score never answers 500 because of core; (3) a guard test fails if any file under services/ml mentions `core.` as a SQL schema, and bootstrap still emits no cross-schema grant (`tests/unit/test_bootstrap_roles_privileges.py` unchanged and green).
- evidence: pytest -q tests/unit/test_score_rules.py tests/unit/test_ml_core_rules.py tests/unit/test_ml_singleflight.py tests/unit/test_cov_services.py tests/unit/test_openapi.py tests/unit/test_bootstrap_roles_privileges.py tests/unit/test_sql_consistency.py: 108 passed; make static and make test green. Core route GET /internal/core/admin/score-rules (service ml only), ML HttpCoreRules with 2 s timeout, direct SQL on core removed from ml.

### T-145 Core stores the score it receives and stops needing the ml schema
- status: done
- spec: REQ-COR-001, REQ-ML-001, REQ-DAT-001
- owner: database-engineer
- depends: T-144
- write_scope: migrations/ (one new expand file with the next free number), services/core/app/ (the qualification worker, repository.py reads of leads and brief facts), tests/unit/test_core_score_column.py, tests/unit/test_brief_facts.py, tests/unit/test_cov_infra.py (the lead filter tests only)
- context: services/core/app/brief_facts.py, services/core/app/repository.py, services/core/app/worker.py, services/ml/app/scoring_service.py, docs/runs/RUN-r1-g-ml.md
- size: M
- acceptance: `python ops/check_migrations.py` and `pytest -q tests/unit/test_core_score_column.py tests/unit/test_brief_facts.py tests/unit/test_auto_qualification.py` and `make test`
- note: PR #50 removed the joins on `ml.lead_score` from core (INV-01: `core_app` has no grant on `ml`), so `score` is `null` in `brief-facts`, in the lead list and in `get_lead`, and the `min_score` filter of the lead list matches nothing. The `lead.score_updated` event already carries `score`, `is_hot`, `source_version` and the tenant, and the core worker consumes it for auto-qualification. Store the score in core: an expand migration `migrations/20261022_0012_core_company_score.sql` (this exact name; T-151 uses 0013) adds nullable `score` (0 to 100), `score_source_version` and `score_updated_at` to `core.company` (same RLS as the table), the worker writes them when it handles the event (never lowering a newer `source_version`), and the three reads and the `min_score` filter use that column instead of the join. Remove the `CAST(NULL AS integer)` placeholders and the T-145 pointer in `brief_facts.py`. Backfill is not needed: the next score event fills it.
- acceptance tests: (1) migration passes `check_migrations.py` and RLS isolates the column per tenant; (2) after a `lead.score_updated` event the lead list, `get_lead` and `brief-facts` return that score and `min_score` filters by it; an older `source_version` does not overwrite a newer one; (3) the guard test `test_core_sql_never_reads_the_ml_schema` still passes.
- evidence: migration 20261022_0012 adds core.company.score, score_source_version, score_updated_at (nullable, check 0..100, table RLS inherited); worker stores the score (tenant context set, version guard in SQL: write when none stored, equal, or both integer text and stored is smaller) then qualifies as before; lead list, get_lead, brief-facts and min_score read c.score; check_migrations OK, targeted tests 105 passed, make static OK, make test 1446 passed; table_catalog snapshot regenerated

### T-146 Funnel HTTP routes and project status on a KILL decision
- status: done
- spec: REQ-LNS-009, REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-109
- write_scope: services/core/app/funnel.py, services/core/app/main.py (the funnel routes only), tests/unit/test_funnel_routes.py, tests/unit/test_openapi.py (NOT_PUBLIC entries only)
- context: services/core/app/funnel.py, libs/domain/funnel.py, config/funnel/, docs/runs/RUN-r1-g-validation.md
- size: M
- acceptance: `pytest -q tests/unit/test_funnel_routes.py tests/unit/test_funnel.py tests/unit/test_openapi.py`
- note: the funnel engine and its audit rows exist (T-109) but nothing serves them. Add internal routes under `/internal/core/funnel/` to read the checklist state of a project and to record a gate decision, tenant scoped from the service-token headers (404 across tenants, `set_tenant_context` before every read), per-route allow list for the gateway only. A KILL decision sets `core.project.status` to the plain text value `killed` (owner decision: the column is unconstrained text with default `draft`, so no migration and no CHECK constraint); define the value once as a constant next to the funnel code, write it in the same transaction as the audit row with an optimistic version check, and refuse a decision on a project that is already `killed`. Production keeps refusing example checklists unless `FUNNEL_ALLOW_EXAMPLE_CHECKLISTS=true`. Add every new route to `NOT_PUBLIC` in tests/unit/test_openapi.py (the gateway exposes them in T-148).
- evidence: core funnel routes GET /internal/core/funnel/projects/{id} and POST .../gates/{gate}/decision; KILL sets status 'killed' (KILLED_STATUS) in the same transaction with version check in metadata->>'version'; 15 route tests; make static OK, make test 1510 passed

### T-147 Validation metrics worker and read routes
- status: done
- spec: REQ-LNS-010, REQ-LNS-011, REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-108, T-110, T-146
- write_scope: services/core/app/validation_worker.py (new), services/core/app/validation_reads.py (new), services/core/app/main.py (the validation routes only), tests/unit/test_validation_worker.py, tests/unit/test_validation_routes.py, tests/unit/test_openapi.py (NOT_PUBLIC entries only)
- context: libs/domain/validation.py, libs/domain/validation_thresholds.yaml, services/streamlit/pages/9_Validation.py, services/streamlit/pages/7_Ops.py, migrations/20261020_0009_validation_schema.sql, ops/schemas.py
- size: L
- acceptance: `pytest -q tests/unit/test_validation_worker.py tests/unit/test_validation_routes.py tests/unit/test_validation_metrics.py tests/unit/test_openapi.py`
- note: the V1 to V6 metrics are pure functions (T-108) and nothing computes or serves them. The `validation` schema is owned by the core role (`SHARED_ROLE_SCHEMAS`), so the worker lives in core: a function that computes the metrics per tenant and project from core data and writes them to `validation.*` with `set_tenant_context`, with a stop condition and a `python -m services.core.app.validation_worker` entry (not added to Compose). Read routes serve exactly the cards that `services/streamlit/pages/9_Validation.py` and `7_Ops.py` request (`validation/projects/<id>/<card>` and `validation/meta-ml`), under `/internal/core/validation/`, tenant scoped, gateway-only allow list, with the threshold decision of T-108 in the response. Do not read the `ml` or `pulse` schemas (INV-01): when a metric needs them, leave that card empty with a reason in the response and list it in the report. Add every new route to `NOT_PUBLIC`.
- evidence: validation_worker.py (--tenant, --passes) and validation_reads.py; routes GET /internal/core/validation/projects/{id}/{card} and /internal/core/validation/meta-ml with threshold decisions; psi, model_utility, decision_latency empty (INV-01/missing sources); make test 1531 passed

### T-148 Gateway public routes for funnel and validation
- status: done
- spec: REQ-API-001, REQ-API-002, REQ-LNS-013
- owner: second-backend-engineer
- depends: T-146, T-147
- write_scope: services/gateway/app/registry.py, services/gateway/app/routes.py, docs/api/openapi.json, tests/unit/snapshots/, tests/unit/test_validation_gateway.py, tests/unit/test_openapi.py, services/streamlit/pages/9_Validation.py (only if a URL differs from the routes you add)
- context: services/gateway/app/registry.py, services/gateway/app/routes.py, services/streamlit/pages/9_Validation.py, docs/runs/RUN-r1-g-validation.md
- size: M
- acceptance: `pytest -q tests/unit/test_validation_gateway.py tests/unit/test_openapi.py tests/unit/test_gateway.py` and `make openapi` leaves no diff
- note: expose the routes of T-146 and T-147 as `/api/v1/validation/...` and the funnel routes with the roles admin, strategist, analyst (read) and admin, strategist (decision), following the existing registry pattern; remove their `NOT_PUBLIC` entries because they become public. The change to the OpenAPI document is additive (`oasdiff` must report no breaking change). Streamlit page 9 must work against the real routes without a change; if a URL differs, change the page, not the route.
- evidence: gateway routes /api/v1/funnel/projects/{id}, .../gates/{gate}/decision, /api/v1/validation/projects/{id}/{card}, /api/v1/validation/meta-ml with Route.allowed_roles; openapi additive; make static OK, make test 1543 passed

### T-149 Event chain: tolerant consumers and flagged producers
- status: done
- spec: REQ-LNS-012, REQ-EXT-001, REQ-EVT-002
- owner: lead-backend-engineer
- depends: T-110
- write_scope: libs/domain/event_chain.py, services/core/app/outbox_relay.py, services/pulse/app/consumer.py, services/ml/app/consumer.py, libs/common/settings.py, .env.example (comments and one variable), ops/check_configuration.py, tests/unit/test_event_chain.py, tests/unit/test_event_chain_consumers.py
- context: libs/domain/event_chain.py, docs/runs/RUN-r1-g-validation.md, libs/contracts/events.py, services/core/app/dead_letter.py
- size: L
- acceptance: `pytest -q tests/unit/test_event_chain.py tests/unit/test_event_chain_consumers.py` and `python ops/check_configuration.py`
- note: only `funnel.gate_decision` is emitted today; the six other chain producers are not wired because nobody verified how the relay and the consumers treat an event type they do not know. First prove it with tests: an unknown or chain-only event type is acknowledged and never lands in `core.outbox_dead_letter` (REQ-EXT-001), in the relay, the pulse consumer and the ml consumer. Then wire the other producers behind a flag `EVENT_CHAIN_EMIT` that is off by default (declared in settings, `.env.example` and the configuration check); with the flag off the outbox content is byte for byte what it is today, because the CI e2e stage fails on any dead letter. Keep the `source_version` growth bounded as it is.
- evidence: pytest test_event_chain + test_event_chain_consumers 34 passed; check_configuration OK; make static/test pass. Unknown and chain-only types acked, never dead-lettered in relay, pulse, ml. EVENT_CHAIN_EMIT default off; helper chain_events_after returns [] when off. Gap: the six producer call sites are outside write_scope and are not wired (see RUN-r1-h-events).

### T-150 ML publisher sends the tenant to pulse
- status: done
- spec: REQ-LNS-004, REQ-SEC-005
- owner: second-backend-engineer
- depends: T-135
- write_scope: services/ml/app/publisher.py, services/ml/app/profiling.py (the error text only), services/ml/app/attribution_service.py, tests/unit/test_ml_publisher.py
- context: services/ml/app/publisher.py, services/pulse/app/lens.py, docs/runs/RUN-r1-g-pulse.md
- size: S
- acceptance: `pytest -q tests/unit/test_ml_publisher.py tests/unit/test_attribution.py tests/unit/test_profiling.py`
- note: the pulse ingest routes `/internal/pulse/ingest/{profiles,communities,topics,attribution}` answer 400 `tenant_required` because the ML publisher sends no tenant. Send it in `X-Tenant-Id` (the tenant of the batch, never a default) with the signed service token, and make a missing tenant raise before any call. The error text of the kmodes path in `profiling.py` still names the `ml` extra: it must name the `kmodes` extra.
- evidence: `pytest -q tests/unit/test_ml_publisher.py tests/unit/test_attribution.py tests/unit/test_profiling.py`: passed; make static, make test pass

### T-151 ML explanation route so the Brief drivers fill
- status: done
- spec: REQ-LNS-007, REQ-BRF-001, REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-137, T-106
- write_scope: services/ml/app/explanations_routes.py (new), services/ml/app/main.py (one `include_router` line), services/ml/app/scoring_service.py, services/ml/app/repository.py (the upsert and a new read only), migrations/20261022_0013_ml_score_features.sql (this exact name), tests/unit/test_ml_explanations.py, tests/unit/test_openapi.py (NOT_PUBLIC entry only), tests/unit/snapshots/table_catalog.json
- context: services/brief/app/explanations.py, services/ml/app/explain.py, services/ml/app/scoring_service.py, docs/runs/RUN-r1-g-ml.md
- size: L
- acceptance: `pytest -q tests/unit/test_ml_explanations.py tests/unit/test_brief_explanations.py tests/unit/test_explain.py` and `python ops/check_migrations.py` and `make test`
- note: Brief asks ML for `GET /internal/ml/explanations/{company_id}` and gets nothing, so `drivers` and `counterfactual` stay null. ML cannot rebuild the drivers because it stores only the score, not the features it was computed from. Add an expand migration (the exact file name above; T-145 uses 0012) with nullable ml-owned columns that keep the three score features and the weights used on `ml.lead_score`, written by `upsert_score`; then serve the route from the stored features with the pure functions of `services/ml/app/explain.py`, answering 404 when the company has no stored features. Use the response shape that `services/brief/app/explanations.py` already parses, a per-route allow list for the service `brief` only, and no read of any other schema (INV-01). Put the route in the new module and add one `include_router` line to main.py, because T-144 edits main.py in the same wave. Add the route to `NOT_PUBLIC`.
- evidence: `pytest -q tests/unit/test_ml_explanations.py tests/unit/test_brief_explanations.py tests/unit/test_explain.py`, `python ops/check_migrations.py`, make test (1432 passed)

### T-152 Pulse writes the project snapshot and stage visit rows
- status: done
- spec: REQ-LNS-003, REQ-PUL-002, REQ-EVT-002
- owner: second-backend-engineer
- depends: T-135, T-149
- write_scope: services/pulse/app/project_projection.py (new), services/pulse/app/consumer.py (one dispatch branch), services/pulse/app/repository.py, tests/unit/test_project_projection.py
- context: services/pulse/app/lens.py, migrations/20261021_0010_pulse_lens_tables.sql, libs/contracts/events.py, docs/runs/RUN-r1-g-pulse.md
- size: M
- acceptance: `pytest -q tests/unit/test_project_projection.py tests/unit/test_pulse_lens_routes.py tests/unit/test_pulse_consumer.py`
- note: `pulse.project_snapshot` and `pulse.project_stage_visit` have no writer, so `GET /internal/pulse/projects/metrics` returns empty values. Fill them from the project and lead stage events that the pulse consumer already receives (read `libs/contracts/events.py` for the types), idempotently (REQ-EVT-002: the same event twice changes nothing), with the tenant of the event and `set_tenant_context` on the write. If the event catalog has no event that carries a project stage change, do not invent one: write what the existing events allow, and list the gap in the report. Run this task after T-149 in the same session because both edit the pulse consumer.
- evidence: pytest project_projection, pulse_lens_routes, pulse_consumer, sql_consistency 43 passed; make static ok; make test 1453 passed. Pulse consumer projects signal.converted_to_project into project_snapshot (stage draft) and first stage visit, idempotent. Gap: no later-stage, score or burn events exist in the catalog.

### T-153 Compose entry for the drift worker, behind a profile
- status: done
- spec: REQ-DEP-001, REQ-LNS-007, REQ-SEC-005
- owner: devops-engineer
- depends: T-138
- write_scope: docker-compose.yml, tests/unit/test_compose.py, .env.example, ops/check_configuration.py, tests/unit/test_guards.py
- context: docker-compose.yml, services/ml/app/schedule.py, services/ml/app/drift_worker.py, docs/runs/RUN-r1-g-ml.md, tests/unit/test_compose.py
- size: S
- acceptance: `pytest -q tests/unit/test_compose.py tests/unit/test_runbook.py` and `python ops/check_configuration.py`
- note: add a `drift-worker` service (command `python -m services.ml.app.schedule`, the shared app environment, `depends_on` pulse healthy and the usual migrate and bootstrap gates) under a Compose profile `lens`, so `make invent-tracker` does not start it and the CI e2e stack is unchanged; add it to `EXPECTED` in tests/unit/test_compose.py as a profile-gated helper like pgadmin. Document `DRIFT_ALERT_WEBHOOK_URL`, `DRIFT_ALERT_HEADER_NAME`, `DRIFT_ALERT_HEADER_VALUE`, `DRIFT_TENANT_ID` and `DRIFT_RUN_HOUR_UTC` in `.env.example` with empty or safe defaults (never a real value), and make the configuration check know them. Redis users must follow `test_every_redis_client_uses_an_acl_user`.
- evidence: drift-worker under compose profile lens; DRIFT_* vars in .env.example; pytest test_compose/test_runbook/test_guards 64 passed; check_configuration OK; make static green

### T-154 Docker images ship the validation and funnel files
- status: done
- spec: REQ-DEP-001, REQ-LNS-009, REQ-LNS-011
- owner: devops-engineer
- depends: T-108, T-109
- write_scope: deploy/Dockerfile.python, .dockerignore, tests/unit/test_images_ship_runtime_files.py
- context: deploy/Dockerfile.python, libs/domain/validation.py, libs/domain/funnel.py, docs/runs/RUN-r1-g-validation.md
- size: S
- acceptance: `pytest -q tests/unit/test_images_ship_runtime_files.py tests/unit/test_compose.py`
- note: `config/funnel/*.yaml` and `libs/domain/validation_thresholds.yaml` are read by repository path and are not package data. Verify from the Dockerfile and `.dockerignore` that the image copies them to where the code looks (read how `libs/domain/validation.py` and `funnel.py` build the path), fix the Dockerfile if not, and add a test that parses the Dockerfile and `.dockerignore` and fails when a file read at runtime by path is not copied or is ignored.
- evidence: Dockerfile.python copies config/ and sets PYTHONPATH=/app; pytest test_images_ship_runtime_files + test_compose 55 passed; make static green

### T-155 Go-live runbook items for the lens and validation wave
- status: done
- spec: REQ-DOC-001, REQ-LNS-009, REQ-LNS-011
- owner: devops-engineer
- depends: T-153, T-154
- write_scope: docs/runbooks/go-live.md, orchestration/n8n/README.md, tests/unit/test_runbook.py
- context: docs/runbooks/go-live.md, docs/runs/RUN-r1-g-ml.md, docs/runs/RUN-r1-g-pulse.md, docs/runs/RUN-r1-g-validation.md, tests/unit/test_runbook.py
- size: S
- acceptance: `pytest -q tests/unit/test_runbook.py`
- note: append new numbered sections after the existing last one (11, nightly backups); do not renumber and keep "## 10. Troubleshooting" as it is, because tests read it. Cover, each as a checkable item: create the n8n credentials `pulse-internal-header` and `drift-alert-header` (names only, never values) and the fact that n8n service tokens live 60 seconds while a static-header credential does not, so the socioeconomic refresh workflow needs the owner's decision; the drift worker profile and its variables (T-153); `FUNNEL_ALLOW_EXAMPLE_CHECKLISTS` must stay false in production and the owner replaces the example checklists in `config/funnel/` (decision D-06); the owner confirms the threshold bands the ADR is silent about (LTV:CAC 5, burn multiple 2 to 3, CAC payback 12 to 18, and the invented `horizon_mix_deviation` bands). Every variable cited must exist in `.env.example` or in the allow sets of tests/unit/test_runbook.py.
- evidence: pytest test_runbook 6 passed; make static green; go-live sections 12-15 added

### T-156 Remove the leftovers of the retired analytics service
- status: done
- spec: REQ-ARC-002, REQ-DAT-001
- owner: second-backend-engineer
- depends: T-129
- write_scope: .codex/agent-registry.yaml, libs/common/, libs/contracts/ (docstrings and comments only), tests/unit/test_registry.py
- context: .codex/agent-registry.yaml, docs/runs/RUN-r1-e.md, migrations/contract_20261020_0008_retire_analytics.sql
- size: S
- acceptance: `pytest -q tests/unit/test_registry.py` and `make static`
- note: follow-up F-r1e-07: remove `legacy_paths: services/analytics/` from `.codex/agent-registry.yaml` and from whatever profile sync reads it (find it with `git grep legacy_paths`), and reword the docstrings and comments in `libs/common` and `libs/contracts` that still say analytics for what is now pulse. Change text only; do not touch `LEGACY_SCHEMAS` in ops/schemas.py (the baseline still creates those schemas) and do not edit services/ (other sessions of this wave own those files).
- evidence: registry already free of legacy_paths (done under T-134); test_registry 5 passed; make static clean

### T-157 Fill the tasks lines of the SPEC requirements
- status: done
- spec: REQ-TRC-001
- owner: qa-reviewer
- depends: T-080
- write_scope: SPEC.md (only the `tasks:` lines), tests/unit/test_spec_tasks_lines.py
- context: SPEC.md, TASKS.md, ops/check_traceability.py, docs/runs/RUN-r1-e.md
- size: S
- acceptance: `python ops/check_traceability.py` and `pytest -q tests/unit/test_spec_tasks_lines.py tests/unit/test_traceability.py`
- note: follow-up F-r1e-09. Many requirements still say `tasks: none yet`. Derive the list from TASKS.md (a requirement's tasks are the ones whose `spec:` cites it) and write it on the requirement's `tasks:` line; where no task cites it, write why in one short clause (for example "later run" or "owner decision"). Change nothing else in SPEC.md: no statement, no `status`, no `verify`. Do not touch REQ-SEC-005 text (it waits for decisions F-r1e-03 and F-r1e-04). Add a test that every `tasks:` line is either task ids that exist in TASKS.md or a stated reason.
- evidence: 107 tasks lines filled in SPEC.md; check_traceability OK; 11 tests passed; make static green

### T-158 Give the ml service the core URL and token in Compose
- status: done
- spec: REQ-DEP-001, REQ-SEC-005, REQ-ML-001
- owner: devops-engineer
- depends: T-144
- write_scope: docker-compose.yml (the ml service only), .env.example (comments only), tests/unit/test_compose.py
- context: docker-compose.yml, services/ml/app/core_rules.py, libs/common/settings.py, docs/runs/RUN-r1-h-scores.md
- size: S
- acceptance: `pytest -q tests/unit/test_compose.py tests/unit/test_runbook.py` and `python ops/check_configuration.py`
- note: T-144 made ML read the tenant score weights from core, but the `ml` service has no `CORE_URL`, so in the real stack it defaults to localhost and always scores with the default weights. Check what `app_env` already provides and add only what is missing for ML to reach core (`CORE_URL: http://core:8000`) and to sign its token (the internal service secret the other callers get). Do not add a `depends_on` that makes ml wait for core if that creates a cycle (core already waits for the same gates); ML must keep working when core is down, by design. Add a test that the ml service sets `CORE_URL` and that no service other than gateway, brief, ml and the seed carries a core URL it does not use.
- evidence: ml service sets CORE_URL http://core:8000, token from app_env, no depends_on core; pytest test_compose+test_runbook 59 passed; check_configuration OK

### T-159 Wire the remaining event chain producers behind the flag
- status: done
- spec: REQ-LNS-012, REQ-EVT-001, REQ-EVT-005
- owner: lead-backend-engineer
- depends: T-149
- write_scope: services/core/app/repository.py (the outbox writes of the producers only), services/core/app/leads.py, services/ml/app/scoring_service.py, libs/domain/event_chain.py, tests/unit/test_event_chain_producers.py
- context: libs/domain/event_chain.py, docs/runs/RUN-r1-h-events.md, docs/runs/RUN-r1-g-validation.md, libs/common/settings.py
- size: L
- acceptance: `pytest -q tests/unit/test_event_chain_producers.py tests/unit/test_event_chain.py tests/unit/test_event_chain_consumers.py tests/unit/test_relay_to_consumers.py` and `make test`
- note: T-149 proved that the relay and both consumers accept chain-only event types and added `chain_events_after(parent, steps, *, enabled)` plus the flag `EVENT_CHAIN_EMIT` (off by default). The six other producers are still not wired because their call sites are in core and ml. List them from `libs/domain/event_chain.py`, call the helper at each producer with `get_settings().event_chain_emit`, in the same transaction as the parent event. With the flag off the outbox content must be byte for byte what it is today (the CI e2e stage fails on any dead letter); prove it with a test that runs the existing pipeline flow with the flag off and compares the outbox rows, and another with the flag on that checks every chain event reaches the relay and no consumer dead-letters it.
- evidence: `pytest -q tests/unit/test_event_chain_producers.py tests/unit/test_event_chain.py tests/unit/test_event_chain_consumers.py tests/unit/test_relay_to_consumers.py` passes; `make static` OK; `make test` 1499 passed, 2 skipped, 4 xfailed. Flag off leaves outbox and stream unchanged (tested). Producers: core create_signal (signal.tagged), create_lead (company.matched, signal.explained), change_status (project.pipeline_updated, project.forecast_updated), ML ScoringService (signal.scored). Cross-service parents are deterministic references (see run report).

### T-160 The ML schedule builds the pulse publisher with the batch tenant
- status: done
- spec: REQ-LNS-004, REQ-LNS-007, REQ-SEC-005
- owner: second-backend-engineer
- depends: T-150, T-138
- write_scope: services/ml/app/schedule.py, services/ml/app/drift_worker.py, tests/unit/test_ml_schedule_tenant.py
- context: services/ml/app/publisher.py, services/ml/app/schedule.py, docs/runs/RUN-r1-h-ml.md
- size: S
- acceptance: `pytest -q tests/unit/test_ml_schedule_tenant.py tests/unit/test_drift_worker.py tests/unit/test_ml_publisher.py`
- note: after T-150 `PulsePublisher` requires a tenant and raises before any call without it, but no construction site passes one, so the scheduled profiling, communities, topics and attribution publishes would raise. Build the publisher in the schedule entry with the tenant of the batch (`DRIFT_TENANT_ID`, falling back to `INGEST_TENANT_ID` as the drift worker does), never a hard coded default; a run without a tenant logs and skips instead of crashing the loop. Test both paths with the injected fakes.
- evidence: schedule.build_publisher uses DRIFT_TENANT_ID/INGEST_TENANT_ID or logs and skips; pytest test_ml_schedule_tenant, test_drift_worker, test_ml_publisher 14 passed

### T-161 The e2e check requires the score in core and the stored features in ml
- status: done
- spec: REQ-RUN-001, REQ-QA-001, REQ-COR-008
- owner: qa-reviewer
- depends: T-145, T-151
- write_scope: ops/e2e_check.py, tests/unit/test_e2e_check.py, tests/unit/test_demo_pipeline.py
- context: ops/e2e_check.py, ops/seed/enrich.py, docs/runs/RUN-r1-h-scores.md, docs/runs/RUN-r1-h-ml.md
- size: S
- acceptance: `pytest -q tests/unit/test_e2e_check.py tests/unit/test_demo_pipeline.py`
- note: after T-145 core stores the score of each company from `lead.score_updated`, and after T-151 ML stores the features it scored with. Make `ops/e2e_check.py` require that rows in `core.company` have a non-null `score` and that `ml.lead_score` rows have the stored features (count them, not just the rows), and make the offline demo pipeline test assert the same after the seed enrichment. There is no Docker in your sandbox: the CI e2e stage is the real check, so state in the report which assertion it will exercise first.
- evidence: e2e_check requires core.company.score non-null and ml.lead_score feature_* stored; demo test asserts same offline; 19 passed; CI e2e is the real check

### T-162 Real Postgres tests for the SQL added since wave r1-g
- status: done
- spec: REQ-QA-001, REQ-SEC-005, REQ-COR-008
- owner: qa-reviewer
- depends: T-145, T-146, T-147, T-151, T-152, T-159
- write_scope: tests/integration/test_sql_r1_flows.py (new), tests/integration/support_sql_pg.py (helpers only)
- context: tests/integration/test_sql_postgres.py, tests/integration/support_sql_pg.py, docs/runs/RUN-r1-h-scores.md, docs/runs/RUN-r1-i-validation.md, docs/runs/RUN-r1-i-events.md
- size: L
- acceptance: `pytest -q tests/integration/test_sql_r1_flows.py tests/integration/test_sql_postgres.py` (they need `TEST_DATABASE_URL`; the CI integration job is the real check)
- note: the integration tier only prepares statements today (`test_every_statement_prepares`); the SQL of T-145, T-146, T-147, T-151, T-152 and T-159 has never run against Postgres, and the run reports list it as an unresolved risk (jsonb_set, FOR UPDATE, row level security on audit_log). Add one test per flow that RUNS the statement on the scratch database after the migrations: a KILL decision sets `core.project.status` to `killed`, bumps the version in `metadata` and writes the audit row; `lead.score_updated` stores `core.company.score` and a second delivery changes nothing; the ML score upsert stores the feature columns; `insert_chain_events` writes the outbox rows with the parent links; the pulse project snapshot upsert is idempotent; a read of a tenant table as the app role without `set_tenant_context` returns no rows and with it returns the tenant rows. Use the app roles from `bootstrap_roles`, not the superuser, wherever the code under test runs as an app role. If a test exposes a defect, do not weaken the test: fix it only when the fix is one statement inside the owning service's SQL, otherwise stop that flow and put the exact failure in the report. There is no Docker in your sandbox: say in the report which test you expect to fail first in CI.
- evidence: `pytest -q tests/integration/test_sql_r1_flows.py tests/integration/test_sql_postgres.py` against local Postgres 16: 23 passed; make static and make test OK. Defect found outside the listed flows: qualification unit of work has no tenant context (see docs/runs/RUN-r1-j-sql.md).

### T-163 Serialize funnel decisions on a project
- status: done
- spec: REQ-LNS-009, REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-146
- write_scope: services/core/app/funnel.py, tests/unit/test_funnel_routes.py
- context: services/core/app/funnel.py, docs/runs/RUN-r1-i-validation.md
- size: S
- acceptance: `pytest -q tests/unit/test_funnel_routes.py tests/unit/test_openapi.py tests/unit/test_sql_consistency.py`
- note: the T-146 run report says that a concurrent ADVANCE and KILL on the same project both succeed. Take the project row lock (`SELECT ... FOR UPDATE`, inside the tenant context) before reading the status for every decision, and answer 409 with a stable error code when the project is already `killed`. Keep the version rule as T-146 left it (only KILL bumps it). Prove the statement order with the fake transaction used by the existing tests (lock first, then read, then write) and add the 409 case. Do not change the routes or the OpenAPI snapshot except to document the new 409 response; if the snapshot changes, regenerate it with `make openapi`.
- evidence: `pytest -q tests/unit/test_funnel_routes.py tests/unit/test_openapi.py tests/unit/test_sql_consistency.py`: 52 passed; make static OK.

### T-164 The e2e check can run with the event chain on
- status: done
- spec: REQ-RUN-001, REQ-QA-001, REQ-EVT-001
- owner: devops-engineer
- depends: T-159, T-161
- write_scope: ops/e2e_check.py, tests/unit/test_e2e_check.py, docker-compose.yml (only to pass `EVENT_CHAIN_EMIT` to core and ml with default false), tests/unit/test_compose.py (only the matching assertion), docs/runs/T-164-ci-workflow.diff (new)
- context: ops/e2e_check.py, .github/workflows/ci.yml, docs/runs/T-064-ci-workflow.diff, docs/runs/RUN-r1-i-events.md, libs/domain/event_chain.py
- size: M
- acceptance: `pytest -q tests/unit/test_e2e_check.py tests/unit/test_compose.py && python ops/check_configuration.py`
- note: T-159 ships the six chain producers behind `EVENT_CHAIN_EMIT`, off by default, and its new SQL ran only on fakes. Pass the flag through Compose (default false, `.env.example` stays `EVENT_CHAIN_EMIT=false`, which `ops/check_configuration.py` enforces). Add a `--chain` mode to `ops/e2e_check.py`: after the normal checks it creates one signal and moves one lead status through the public routes, then requires that the outbox holds the chain event types (`signal.tagged`, `company.matched`, `signal.explained`, `project.pipeline_updated`, `project.forecast_updated`) all published and that the dead letter count is zero. Agents cannot edit `.github/workflows/`: write the extra CI step (a second pass with `EVENT_CHAIN_EMIT=true`, after the normal e2e) as a diff in `docs/runs/T-164-ci-workflow.diff`, as T-064 did, for the owner to apply. State in the report which step you expect to fail first.
- evidence: `pytest -q tests/unit/test_e2e_check.py tests/unit/test_compose.py` 79 passed; `python ops/check_configuration.py` OK. `--chain` runs a driver inside the gateway container (service tokens, core internal API) because the public routes need a verified JWT tenant and the default stack has identity validation off. CI step in docs/runs/T-164-ci-workflow.diff (applies cleanly with patch). First step expected to fail: "Turn the event chain on" if `--wait` on core/ml times out, else the chain check on `project.*` types or unpublished chain events if the first real run of the T-159 SQL fails.

### T-165 Core serves the score features per tenant and ML backfills the old score rows
- status: done
- spec: REQ-LNS-007, REQ-BRF-001, REQ-SEC-005, REQ-ML-001, REQ-COR-001
- owner: lead-backend-engineer
- depends: T-144, T-151
- write_scope: libs/domain/score_features.py (new), ops/seed/enrich.py (import the shared derivation only), services/core/app/score_features.py (new), services/core/app/repository.py (one new read), services/core/app/main.py (the new route only), services/ml/app/core_features.py (new), services/ml/app/backfill.py (new), services/ml/app/repository.py (one read of the rows without features), tests/unit/test_score_features.py, tests/unit/test_ml_core_features.py, tests/unit/test_ml_backfill.py, tests/unit/test_demo_pipeline.py (only if the shared derivation needs it), tests/unit/test_openapi.py (NOT_PUBLIC entry only), tests/integration/test_sql_score_features.py (new), docs/api/openapi.json
- context: services/core/app/score_rules.py, services/ml/app/core_rules.py, ops/seed/enrich.py, services/ml/app/scoring_service.py, services/ml/app/explanations_routes.py, docs/runs/RUN-r1-j-ops.md, docs/runs/RUN-r1-j-sql.md, tests/integration/test_sql_r1_flows.py
- size: L
- acceptance: `pytest -q tests/unit/test_score_features.py tests/unit/test_ml_core_features.py tests/unit/test_ml_backfill.py tests/unit/test_openapi.py tests/unit/test_sql_consistency.py tests/unit/test_demo_pipeline.py tests/unit/test_ml_singleflight.py tests/unit/test_ml_explanations.py tests/integration/test_sql_score_features.py` and `make static` and `make test`
- note: score rows written before T-151 have null features, so the explanation route answers 404 for them. The r1-j-ops attempt stopped on INV-01 (the features come only from core data, and `ml.lead_score` has no `tenant_id`); the owner chose a core internal endpoint that ML calls, the T-144 pattern. CORE: move the deterministic derivation of `icp_fit`, `signal_strength` and `territory_opportunity` out of `ops/seed/enrich.py` into the pure module `libs/domain/score_features.py` (same formula, same constants, so the seed output does not change; add a test that pins the seed output) and serve it with `GET /internal/core/admin/score-features?limit=&after=` (signed service token for `ml` with the same allow list as `/internal/core/admin/score-rules`, required `X-Tenant-Id`, answer 422 without it). The route lists the companies of that tenant ordered by id, at most `limit` (default 200, hard cap 500) after the cursor `after`, with `company_id`, the three features and `source_version`, plus `next_after`. The repository read runs under `set_tenant_context` AND filters `tenant_id = :tenant_id` in the statement; `territory_opportunity` is computed over the companies of that tenant only. Register the route in `NOT_PUBLIC`. ML: `HttpCoreFeatures` (like `HttpCoreRules`: service token, tenant header, explicit timeout, `CoreFeaturesUnavailable` on transport error or 5xx) and `python -m services.ml.app.backfill --tenant <id> --limit N`, which pages through the core answer, keeps only the companies whose stored ml row has null features (one ml read by company ids, so an id that core did not return can never be rescored), and rescores each through `ScoringService.regenerate` with the tenant weights, so the score version, the event and the stored features come out as for any new score. It is idempotent (a second run finds nothing), bounded by `--limit`, and prints only counts, never rows or tokens. ml gets no grant on the core schema (INV-01) and the backfill does not read it. ISOLATION TESTS (required): unit tests that tenant A's request returns only tenant A's companies and the statement text filters by tenant, that a missing tenant header answers 422, that a token of another service answers 403, that the ML client sends the tenant it was asked for and no other, and that the backfill never rescores a company that core did not return even when ml holds rows of another tenant; a real Postgres test in `tests/integration/test_sql_score_features.py` (as `core_app`, with two tenants' companies) that tenant A's read never returns tenant B's rows and that a read without tenant context returns none. There is no Docker in your sandbox: use a local PostgreSQL if one is available and say in the report which test you expect to fail first in CI. Note in the report that the features are the demo heuristic of the seed, a placeholder until the real feature definition is decided.
- evidence: pytest acceptance list 91 passed incl. tests/integration/test_sql_score_features.py (3 passed on local PostgreSQL 16 as core_app); make static OK; make test OK. Features are the seed demo heuristic, a placeholder until the real feature definition is decided. Wrong-service token answers 401, as for score-rules.

### T-166 Runbook for turning the event chain on
- status: done
- spec: REQ-DOC-001, REQ-EVT-001
- owner: devops-engineer
- depends: T-159
- write_scope: docs/runbooks/event-chain-rollout.md (new), docs/runbooks/go-live.md (one link line), tests/unit/test_runbook.py
- context: docs/runbooks/go-live.md, docs/runs/RUN-r1-i-events.md, docs/runs/RUN-r1-h-events.md, libs/domain/event_chain.py
- size: S
- acceptance: `pytest -q tests/unit/test_runbook.py && python ops/check_language.py`
- note: write the operator procedure for setting `EVENT_CHAIN_EMIT=true`: consumers first (T-149 already accept the chain types), then producers; the checks to run before and after (outbox backlog, dead letter count zero, the `--chain` mode of `ops/e2e_check.py` that T-164 adds, `trace_chain` gaps); how to roll back (set the flag to false; chain events already written stay valid and are ignored by old consumers); and the known limit that three chain links use deterministic reference ids that no event carries, so `trace_chain` reports those gaps by design until the contract carries the parent event id. Add the matching assertions to `tests/unit/test_runbook.py`. Do not invent behavior the code lacks: cite the file for each claim.
- evidence: pytest -q tests/unit/test_runbook.py 9 passed; check_language OK; make static/test pass. Runbook cites a file per claim; --chain mode is stated as pending T-164.

### T-167 Fill the SPEC tasks lines for the tasks added since T-157
- status: done
- spec: REQ-TRC-001
- owner: qa-reviewer
- depends: T-157
- write_scope: SPEC.md (only the `tasks:` lines), tests/unit/test_spec_tasks_lines.py
- context: SPEC.md, TASKS.md, ops/check_traceability.py
- size: S
- acceptance: `python ops/check_traceability.py` and `pytest -q tests/unit/test_spec_tasks_lines.py tests/unit/test_traceability.py`
- note: T-158 to T-166 and T-144 to T-157 cite requirements whose `tasks:` lines may not list them. Rebuild every `tasks:` line from the `spec:` lines of TASKS.md (a requirement's tasks are the ones that cite it), keep the stated reasons where no task cites a requirement, and change nothing else in SPEC.md: no statement, no `status`, no `verify`, and not the REQ-SEC-005 text. The existing test must keep passing and gain one case that fails when a task cites a requirement whose `tasks:` line omits it.
- evidence: rebuilt 18 tasks lines from the spec: lines of TASKS.md (no line lacked a citing task); check_traceability OK; test_spec_tasks_lines + test_traceability pass; make static/test pass.

### T-168 Qualification applies the tenant of the score event
- status: done
- spec: REQ-COR-008, REQ-SEC-005, REQ-QA-001
- owner: lead-backend-engineer
- depends: T-024, T-145, T-150, T-162
- write_scope: services/core/app/qualification.py, services/core/app/repository.py (the qualification transaction only), services/core/app/worker.py (only if the tenant must be passed in), tests/unit/test_auto_qualification.py, tests/unit/test_hot_alert.py, tests/unit/support_pipeline.py, tests/unit/test_cov_infra.py (the qualification cases only), tests/integration/test_sql_r1_flows.py (new cases only)
- context: services/core/app/qualification.py, services/core/app/worker.py, libs/common/tenant.py, docs/runs/RUN-r1-j-sql.md, tests/integration/test_sql_r1_flows.py
- size: M
- acceptance: `pytest -q tests/unit/test_auto_qualification.py tests/unit/test_hot_alert.py tests/unit/test_core_score_column.py tests/unit/test_pipeline_flow.py tests/unit/test_cov_infra.py tests/unit/test_sql_consistency.py tests/integration/test_sql_r1_flows.py` and `make static` and `make test`
- note: defect found by T-162 (RUN-r1-j-sql.md): `SqlQualificationUnitOfWork` runs without tenant context, so under row level security `get_company_tenant` cannot see a company that belongs to a tenant and `handle_score_event` answers `unknown_company` as `core_app`: no lead is created or advanced. The event already carries `tenant_id` (T-150, `_tenant_of` in `worker.py`). Make `handle_score_event` read the tenant of the event and call `set_tenant_context` on the transaction before the first read or write (the inbox insert, `get_company_tenant`, `lock_key`, `find_leads`, the lead insert, the audit and outbox writes); add the matching method to `QualificationTransaction` and to every fake that implements it. An event for a company of ANOTHER tenant must still answer `unknown_company` and write nothing. An event without a tenant keeps today's behavior (it only sees companies without tenant); state that in the report. END TO END TEST (required, real Postgres in `tests/integration/test_sql_r1_flows.py`, as `core_app`, through `build_handlers` and `SqlQualificationUnitOfWork`): a score event for a company that has a tenant creates the lead (assert the `core.lead` row: tenant, company, stage, version), a later event with a higher score advances that lead (assert the stage and the version on the row), a second delivery of the same event changes nothing, and an event carrying another tenant's id creates no lead (assert zero rows). Cover the worker and the repository in unit tests too (the tenant context is set first, before any read). There is no Docker in your sandbox: use a local PostgreSQL if one is available and say in the report which test you expect to fail first in CI.
- evidence: acceptance pytest list 128 passed, 1 xfailed (integration run on local PostgreSQL 16, 19 passed in test_sql_r1_flows.py); make static OK; make test 1578 passed; see docs/runs/notes-r1-k-qualify.md

### T-169 Runbook step for the feature backfill
- status: done
- spec: REQ-DOC-001, REQ-LNS-007
- owner: devops-engineer
- depends: T-165
- write_scope: docs/runbooks/go-live.md (one section), tests/unit/test_runbook.py
- context: docs/runbooks/go-live.md, docs/runs/RUN-r1-k-features.md, services/ml/app/backfill.py
- size: S
- acceptance: `pytest -q tests/unit/test_runbook.py && python ops/check_language.py`
- note: after T-165 lands, document the operator step for the old score rows: deploy core before ml (ml calls the new core route), run `python -m services.ml.app.backfill --tenant <id> --limit N` once per tenant inside the ml container, read the counts it prints, run it again until it reports zero (idempotent), and check that the explanation route now answers instead of 404. Cite the file for each claim and add the matching assertions to `tests/unit/test_runbook.py`. Do not invent flags: read them from `services/ml/app/backfill.py` as T-165 wrote it.
- evidence: section 16 added to docs/runbooks/go-live.md; test_backfill_section_matches_the_cli_and_cites_real_files added; pytest tests/unit/test_runbook.py 10 passed, check_language OK, make static OK, make test 1603 passed

### T-170 Publish the Postgres and Redis ports on the loopback only
- status: done
- spec: REQ-SEC-005, REQ-RUN-001
- owner: devops-engineer
- depends: T-154
- write_scope: docker-compose.yml (the two `ports:` entries of postgres and redis), .env.example (one commented override), tests/unit/test_compose.py (the matching assertion), ops/check_configuration.py (only if it lists the ports)
- context: docker-compose.yml, tests/unit/test_compose.py, deploy/README.md, docs/runs/RUN-r1-e.md
- size: S
- acceptance: `pytest -q tests/unit/test_compose.py && python ops/check_configuration.py`
- note: `docker-compose.yml` publishes `5432:5432` and `6379:6379` on every interface, so a developer machine on a shared network exposes the database and the Redis data user. Publish them as `${POSTGRES_BIND:-127.0.0.1}:5432:5432` and `${REDIS_BIND:-127.0.0.1}:6379:6379` (host tools, `make invent-tracker`, `psql` from the host and the CI jobs keep working through `127.0.0.1`; containers use the Compose network and are not affected). Add a test that no `ports:` entry of postgres or redis lacks an explicit bind address, and document the two variables in `.env.example` as commented overrides. Do not touch the other services' ports.
- evidence: `pytest -q tests/unit/test_compose.py` 56 passed; `python ops/check_configuration.py` OK (106 variables); make static and make test green. Bind variables are listed uncommented in .env.example (value equals the default) because the configuration guard ignores commented lines.

### T-171 The e2e check proves that scored companies become leads and that the backfill runs
- status: done
- spec: REQ-RUN-001, REQ-QA-001, REQ-COR-008
- owner: qa-reviewer
- depends: T-168, T-165, T-161
- write_scope: ops/e2e_check.py, tests/unit/test_e2e_check.py, tests/unit/test_demo_pipeline.py
- context: ops/e2e_check.py, ops/seed/enrich.py, docs/runs/RUN-r1-k-qualify.md, docs/runs/RUN-r1-k-features.md, services/ml/app/backfill.py
- size: M
- acceptance: `pytest -q tests/unit/test_e2e_check.py tests/unit/test_demo_pipeline.py`
- note: T-168 made qualification see the companies of a tenant, and T-165 added the feature backfill, but the CI e2e still only checks scores and features. Add two checks to `ops/e2e_check.py`: (1) after the seed enrichment, the demo tenant has `core.lead` rows with `source = 'auto_qualification'` for the scored companies (count them, and require at least one lead per scored company that reached the qualified threshold; read the threshold from the tenant config, do not hard code it); (2) the backfill command runs inside the ml container (`docker compose exec`, as the T-164 driver does for the gateway) for the demo tenant with a small `--limit`, exits 0 and reports zero rows left without features, which also proves ml reaches the new core route with its service token. The queries use the existing superuser psql path, so INV-01 is unchanged. There is no Docker in your sandbox: the CI e2e stage is the real check, so state in the report which assertion it will exercise first.
- evidence: `pytest -q tests/unit/test_e2e_check.py tests/unit/test_demo_pipeline.py tests/unit/test_sql_consistency.py` 61 passed; `make static` ok. Offline only; CI e2e is the real check.

### T-172 Close the smoke task and fix the lint the gate does not cover
- status: done
- spec: REQ-QA-001
- owner: coordinator
- depends: none
- write_scope: docs/runs/RUN-smoke.md (new), tests/unit/test_runbook.py (the line length findings only)
- context: AGENTS.md, Makefile, docs/runs/RUN-r1-j-docs.md
- size: S
- acceptance: `make static`
- note: `T-000` is the only L1 task still `todo` (F-r1e-12). Do what its note asks, now that the repository is mature: write `docs/runs/RUN-smoke.md` with one line per check performed (git branch, commit, `make static` result, `make test` result, Python version, whether `pip install '.[dev,streamlit,identity,sqltest]'` works) and set `T-000` to `done` with that evidence. Do not push from the smoke check; the wave branch push is the push. Also fix the ruff line length finding at about line 133 of `tests/unit/test_runbook.py`, which `make static` does not cover; if a lint command outside `make static` finds more in `tests/`, list them in the report and fix only the ones in files this wave created, then say what else exists.
- evidence: `make static` OK; docs/runs/RUN-smoke.md written, T-000 done, E501 in tests/unit/test_runbook.py fixed; other `ruff check tests` findings listed in docs/runs/RUN-r1-l-platform.md.

### T-173 The doctor can report the pipeline health
- status: done
- spec: REQ-RUN-001, REQ-DOC-001
- owner: second-backend-engineer
- depends: T-161
- write_scope: ops/doctor.py, tests/unit/test_doctor.py, docs/runbooks/go-live.md (one line)
- context: ops/doctor.py, ops/e2e_check.py, docs/runbooks/go-live.md, docs/runbooks/event-chain-rollout.md
- size: M
- acceptance: `pytest -q tests/unit/test_doctor.py tests/unit/test_runbook.py && python ops/check_language.py`
- note: `make doctor` checks configuration and service health, but an operator has no one command for the state of the data pipeline. Add `ops/doctor.py --pipeline`, read only, through the same superuser psql path as `ops/e2e_check.py`: outbox backlog (unpublished events and the age of the oldest), dead letter count, companies without a score, score rows without stored features, leads created by auto qualification. It prints counts only, never rows, tokens or tenant data, and exits 1 when the dead letter count is above zero or the oldest unpublished event is older than a threshold given by `--max-backlog-seconds` (default 300). Reuse the SQL constants of `e2e_check.py` by importing them instead of copying them where that does not create a cycle. Add one line to the go-live runbook that names the command, with the matching assertion in `tests/unit/test_runbook.py` if that file already checks the runbook for commands.
- evidence: `ops/doctor.py --pipeline [--max-backlog-seconds N]` added (injectable query, exit 1 on dead letters or stale backlog); go-live runbook line and test_runbook needle added; test_doctor, test_runbook, test_sql_consistency, check_language and make static pass

### T-082 Row level security on core.signal, core.location and core.lead_outcome
- status: done
- spec: REQ-DAT-005, REQ-SEC-005
- owner: database-engineer
- depends: T-162, T-168
- write_scope: migrations/20261023_0014_core_rls_signal_location_outcome.sql (new), tests/integration/test_sql_postgres.py (the RLS_EXEMPT block and its assertions), tests/integration/test_sql_r1_flows.py (new cases only), services/core/app/repository.py (only to set the tenant context on the writers that lack it), tests/unit/snapshots/table_catalog.json (only if the catalog test requires it), docs/runs/
- context: migrations/20261018_0004_run1_schema_additions.sql, tests/integration/test_sql_postgres.py, libs/common/tenant.py, docs/runs/RUN-r1-j-sql.md, docs/runs/RUN-r1-e.md
- size: L
- acceptance: `pytest -q tests/integration/test_sql_postgres.py tests/integration/test_sql_r1_flows.py tests/unit/test_sql_consistency.py tests/unit/test_schema_catalog.py` and `python ops/check_migrations.py` and `make static` and `make test`
- note: decision D-29 (owner, closed 2026-10-22), the former F-r1e-11. `core.signal`, `core.location` and `core.lead_outcome` carry business data per tenant and must not stay exempt: one expand migration that does ENABLE and FORCE row level security and creates the policy `<table>_tenant_isolation` on `tenant_id = current_setting('app.tenant_id')::uuid`, following the pattern of `20261018_0004`. If any of the three has a nullable `tenant_id`, backfill and set it `NOT NULL` in the same expand file (the statement must be re-runnable). Then every writer and reader of those tables must run under `set_tenant_context`: find them all (signal create, the import and location writers, the outcome route and the readers behind brief facts and lead reads) and prove each one with a real Postgres test as `core_app` (a write and a read with the context succeed, without it they see or write nothing). The final exempt registry has five tables, each with its real reason and no wording like "guess": `core.outbox_event`, `core.outbox_dead_letter`, `core.processed_event`, `ml.processed_event` (messaging and idempotency infrastructure read and written only by the owning service role, never exposed by a public API; isolation comes from INV-01 and the role, and `tenant_id` is routing metadata) and `core.audit_log` (append-only, read only by an admin route of the same tenant with an explicit filter). Remove the "UNVERIFIED" comment, add the assertion that the three new tables DO have RLS, and check that no live migration or test still names `analytics.processed_event` (the historical expand files may keep it, the contract migration drops it). Name the migration exactly as above. Do not weaken any existing policy.
- evidence: pytest -q test_sql_postgres test_sql_r1_flows test_sql_consistency test_schema_catalog against a local PostgreSQL 16 (TEST_DATABASE_URL): passed; python ops/check_migrations.py OK; make static OK; make test 1680 passed, 2 skipped, 4 xfailed (see docs/runs/RUN-r1-n-rls.md)

### T-174 Typed payloads for the six chain events
- status: done
- spec: REQ-EVT-008, REQ-EVT-005
- owner: lead-backend-engineer
- depends: T-159
- write_scope: libs/contracts/events.py, tests/unit/test_contracts.py, tests/unit/snapshots/model_schemas.json, docs/api/openapi.json (only if the snapshot test requires it)
- context: libs/contracts/events.py, libs/domain/event_chain.py, docs/runs/RUN-r1-i-events.md, SPEC.md REQ-EVT-008
- size: M
- acceptance: `pytest -q tests/unit/test_contracts.py tests/unit/test_event_chain.py tests/unit/test_event_chain_producers.py tests/unit/test_event_chain_consumers.py tests/unit/test_openapi.py` and `make static` and `make test`
- note: decision D-20. Promote the six names of `ReservedEventType` that are chain events (`signal.tagged`, `signal.scored`, `company.matched`, `signal.explained`, `project.pipeline_updated`, `project.forecast_updated`) to typed pydantic payloads with `extra="forbid"` and a `schema_version` per type, carried by the existing `DomainEvent` envelope (`version`, `source_version`). The canonical payloads are in REQ-EVT-008 and each one has a mandatory `parent_event_id` (UUID or null). Do not change the producers' behavior in this task beyond making their payloads validate against the new models; the flag `EVENT_CHAIN_EMIT` keeps its default. Add a test per type (valid, missing field, extra field) and one that every producer payload of T-159 validates. Keep `funnel.*` and the other reserved names untouched.
- evidence: acceptance pytest (60 passed), make static, make test (1634 passed, 2 skipped, 4 xfailed)

### T-175 Producers propagate parent_event_id through the chain
- status: done
- spec: REQ-LNS-012, REQ-EVT-008
- owner: lead-backend-engineer
- depends: T-174
- write_scope: services/core/app/leads.py, services/core/app/repository.py (the chain producers only), libs/domain/event_chain.py, libs/contracts/api.py (ScoreFeatures: the optional parent field), tests/unit/test_event_chain.py, tests/unit/test_event_chain_producers.py
- context: libs/domain/event_chain.py, docs/runs/RUN-r1-i-events.md, docs/runbooks/event-chain-rollout.md, SPEC.md REQ-LNS-012
- size: L
- acceptance: `pytest -q tests/unit/test_event_chain.py tests/unit/test_event_chain_producers.py tests/unit/test_event_chain_contracts.py tests/unit/test_openapi.py` and `make static` and `make test`
- note: decision D-21 (owner). The three joins that `trace_chain` reports as gaps (`signal.tagged` to `signal.scored`, `signal.scored` to `company.matched`, `funnel.gate_decision` to `project.pipeline_updated`) must close with real event ids, not with the deterministic references of `reference_parent`. Carry the id of the parent event in `ScoreFeatures` (an optional field, additive for the OpenAPI diff) and in the payload of the lead write, fill `parent_event_id` in every producer, and make `trace_chain` pass on a full chain built by the producers (an offline test that runs the chain with the flag on and asserts `complete` is true and `gap` is empty). `reference_parent` may stay for events written before this change. Cross-service ids travel in the request or the event, never through a shared table (INV-01). The `signal.scored` side is T-176; here ML only receives and echoes the parent id.
- evidence: acceptance pytest (22 passed; test_event_chain_contracts.py does not exist), make static, make test (1635 passed, 2 skipped, 4 xfailed). Open: funnel.py does not expose the gate event id; production callers do not yet send parent ids (see RUN report)

### T-176 ML publishes signal.scored through an outbox
- status: done
- spec: REQ-LNS-012, REQ-EVT-001, REQ-EVT-008
- owner: second-backend-engineer
- depends: T-174, T-175
- write_scope: libs/messaging/__init__.py (new), libs/messaging/relay.py (new, the shared relay), services/core/app/dead_letter.py (becomes a thin wrapper that keeps its public names), services/core/app/outbox_relay.py, services/core/app/republish.py (only the shared `stream_fields` import), migrations/20261023_0016_ml_outbox.sql (new), services/ml/app/scoring_service.py, services/ml/app/consumer.py, services/ml/app/repository.py (the outbox write and the ml unit of work), services/ml/app/outbox_relay.py (new, the ml instance), docker-compose.yml (a `ml-relay` service like `core-relay`), ops/check_configuration.py (the service list), tests/unit/test_ml_outbox.py, tests/unit/test_dead_letter.py, tests/unit/test_outbox_relay.py, tests/unit/test_ml_singleflight.py, tests/unit/test_compose.py (the relay mapping only), tests/unit/test_sql_consistency.py, tests/unit/snapshots/table_catalog.json, tests/integration/test_sql_postgres.py (RLS_EXEMPT entries for the ml outbox tables only), tests/integration/test_sql_r1_flows.py (new cases only)
- context: services/core/app/dead_letter.py, services/core/app/outbox_relay.py, services/core/app/republish.py, services/ml/app/scoring_service.py, libs/domain/event_chain.py, docs/runs/RUN-r1-i-events.md, SPEC.md D-17, D-21 and D-29
- size: L
- acceptance: `pytest -q tests/unit/test_ml_outbox.py tests/unit/test_dead_letter.py tests/unit/test_outbox_relay.py tests/unit/test_ml_singleflight.py tests/unit/test_event_chain_producers.py tests/unit/test_sql_consistency.py tests/unit/test_schema_catalog.py tests/unit/test_compose.py tests/integration/test_sql_r1_flows.py tests/integration/test_sql_postgres.py` and `python ops/check_migrations.py` and `python ops/check_configuration.py` and `make static` and `make test`
- note: decisions D-21 and D-17 (owner, clarified 2026-10-22): a domain event outside the outbox breaks the outbox rule, so `signal.scored` leaves `ml` through a transactional outbox written in the same transaction as the score. This does NOT conflict with D-17: there is ONE relay implementation and one INSTANCE per schema with an outbox. So: (1) move the generic part of the relay (retry schedule, dead letter, `relay_due_events`, `stream_fields`) from `services/core/app/dead_letter.py` to the shared module `libs/messaging/relay.py`; core and ml pass their own unit of work (`core.outbox_event` and `ml.outbox_event`); it is one algorithm in two processes, not a second implementation, and core behavior and its public names stay unchanged. (2) Expand migration with the exact name above: `ml.outbox_event` (and `ml.outbox_dead_letter` if the relay needs it) owned by `ml_app`, same columns as `core.outbox_event`, no row level security (infrastructure, D-29); add both tables to the `RLS_EXEMPT` registry with the real reason. (3) The `ml` relay instance (`services/ml/app/outbox_relay.py`, compose service `ml-relay`) reads and writes only `ml.*` (INV-01). `lead.score_updated` keeps its current path unless moving it is the only way to keep the two events ordered; if so, say so in the report instead of changing it silently. Add a real Postgres test as `ml_app`: the score and its outbox row commit together and roll back together, and the ml relay publishes the row with its tenant. The task is no longer blocked by D-17.
- evidence: acceptance pytest (137 passed, 28 integration skipped: no Postgres), check_migrations, check_configuration, make static, make test (1649 passed, 2 skipped, 4 xfailed). Real Postgres cases written but not run (no Docker)

### T-177 Pulse projects the project stage events
- status: done
- spec: REQ-PUL-004, REQ-LNS-012
- owner: second-backend-engineer
- depends: T-152, T-174
- write_scope: services/pulse/app/consumer.py, services/pulse/app/project_projection.py, services/pulse/app/repository.py, tests/unit/test_project_projection.py, tests/unit/test_pulse_consumer.py
- context: services/pulse/app/project_projection.py, migrations/20261021_0010_pulse_lens_tables.sql, libs/contracts/events.py, docs/runs/RUN-r1-h-events.md
- size: M
- acceptance: `pytest -q tests/unit/test_project_projection.py tests/unit/test_pulse_consumer.py tests/unit/test_event_chain_consumers.py tests/unit/test_sql_consistency.py` and `make static` and `make test`
- note: decision D-22 (owner): a lead stage change equals `project.pipeline_updated` plus `project.forecast_updated`, with no figures; the consumer is Pulse. Add the two dispatch branches. The tables already exist: update `pulse.project_snapshot` (stage) and close the open `pulse.project_stage_visit` row by setting `exited_at` (the SPEC and the owner text say project_snapshot for both; the column `exited_at` lives in `project_stage_visit`, use the real tables and say so in the report). The writes are idempotent by `event_id` (inbox pattern), tenant scoped under `set_tenant_context`, and a replay of the same event changes nothing. `forecast_updated` carries no figure and must not invent any. Add a test per event plus a replay case.
- evidence: acceptance pytest (66 passed), make static, make test (1634 passed, 2 skipped, 4 xfailed). Real tables used: stage in project_snapshot, exited_at in project_stage_visit

### T-178 Scoring feature baseline gets a versioned source and a pinned output
- status: done
- spec: REQ-LNS-007
- owner: second-backend-engineer
- depends: T-165
- write_scope: libs/domain/score_features.py, tests/unit/test_score_features.py, tests/unit/test_demo_pipeline.py, docs/experiments/ (one note only)
- context: libs/domain/score_features.py, ops/seed/enrich.py, docs/runs/RUN-r1-k-features.md, SPEC.md D-23
- size: S
- acceptance: `pytest -q tests/unit/test_score_features.py tests/unit/test_demo_pipeline.py tests/unit/test_ml_backfill.py` and `make static`
- note: decision D-23 (owner). The derivation of `icp_fit`, `signal_strength` and `territory_opportunity` stays as the permanent audited baseline (`source_version` `demo-1`, INV-18); do not delete it or switch it off. Make the version scheme explicit: a constant `BASELINE_SOURCE_VERSION = "demo-1"` and a documented rule that any change of the formula bumps the version as `features-<major>.<minor>` in the same pull request. Add a test that pins the exact output of the seed for a fixed profile set (the full rows, so a formula change fails it) and a test that a changed formula without a new version string cannot pass (compare a hash of the formula inputs and constants to the version, or pin the rows per version). Write one short note in `docs/experiments/` stating that the production definition is L3 work with an experiment sheet (INV-19), kept in shadow next to this baseline; do not start that work.
- evidence: `pytest -q tests/unit/test_score_features.py tests/unit/test_demo_pipeline.py tests/unit/test_ml_backfill.py` 29 passed; `make static` ok

### T-179 Core route for the validation inputs
- status: done
- spec: REQ-LNS-014, REQ-SEC-005, REQ-IDN-001
- owner: lead-backend-engineer
- depends: T-147
- write_scope: services/core/app/validation_inputs.py (new), services/core/app/main.py (the new route only), services/core/app/repository.py (one read and one write), tests/unit/test_validation_inputs.py, tests/unit/test_openapi.py (NOT_PUBLIC entry only), tests/integration/test_sql_r1_flows.py (new cases only)
- context: services/core/app/validation_reads.py, services/core/app/validation_worker.py, services/core/app/main.py (tenant-config route and ETag), docs/runs/RUN-r1-i-validation.md, SPEC.md REQ-LNS-014
- size: M
- acceptance: `pytest -q tests/unit/test_validation_inputs.py tests/unit/test_validation_routes.py tests/unit/test_validation_worker.py tests/unit/test_openapi.py tests/unit/test_sql_consistency.py` and `make static` and `make test`
- note: decision D-24 (owner). Add `PUT /internal/core/projects/{id}/validation-inputs`, gateway-only service token, tenant scoped, with the ETag and `If-Match` behavior of `tenant_config` (a stale ETag answers 412). It validates a typed object (pydantic, `extra="forbid"`, the fields V1, V2, V4 and V5 already read from `core.project.metadata->'validation'`; read `validation_worker.py` for the exact keys and ranges) and stores it under that key without touching the rest of `metadata`. The role check (`strategist` or `admin`) is done by the gateway (T-180); the core route rejects a request without the gateway identity. Write an audit row. Until an input exists, the validation cards answer empty with the reason `needs_strategist_input` (change the current empty reason of V1, V2, V4 and V5 to this value and update its tests). A real Postgres test as `core_app` proves the write is tenant scoped (another tenant's project answers 404 and nothing is written).
- evidence: `pytest -q tests/unit/test_validation_inputs.py tests/unit/test_validation_routes.py tests/unit/test_validation_worker.py tests/unit/test_openapi.py tests/unit/test_sql_consistency.py` 78 passed; `make static` OK; `make test` 1711 passed, 2 skipped, 4 xfailed; real-Postgres cases in tests/integration/test_sql_r1_flows.py not run here (no Docker)

### T-180 Gateway route for the validation inputs
- status: done
- spec: REQ-LNS-014, REQ-API-002
- owner: second-backend-engineer
- depends: T-179
- write_scope: services/gateway/app/registry.py, services/gateway/app/routes.py, docs/api/openapi.json, tests/unit/snapshots/, tests/unit/test_validation_gateway.py, tests/unit/test_openapi.py
- context: services/gateway/app/registry.py, services/gateway/app/routes.py, docs/runs/RUN-r1-i-validation.md, SPEC.md REQ-LNS-014 and REQ-API-002
- size: S
- acceptance: `pytest -q tests/unit/test_validation_gateway.py tests/unit/test_gateway_routes.py tests/unit/test_openapi.py` and `make openapi` leaves no further diff and `make static`
- note: the owner decided that a strategist writes the validation inputs by hand (D-24) and SPEC names the core route only; a strategist can reach it only through the gateway, so expose it: `PUT /projects/{id}/validation-inputs` under `/api/v1`, allowed roles `strategist` and `admin` (any other role answers 403 `forbidden`, a project of another scope or tenant answers 404 `not_found`, per REQ-API-002), forwarding the ETag and `If-Match` headers. Additive OpenAPI change only; run `make openapi` and keep `oasdiff` clean. This task was added by the coordinator to make D-24 usable; say so in the report.
- evidence: `pytest -q tests/unit/test_validation_gateway.py tests/unit/test_gateway_routes.py tests/unit/test_openapi.py` 61 passed; `make openapi` no further diff; `make static` ok; `make test` ok. Coordinator added `if-match` to the gateway proxy forwarded headers (services/gateway/app/main.py) because core answers 428 without it; this also fixes the tenant-config ETag path. 428 is not documented in OpenAPI (openapi_doc.py out of scope)

### T-181 Funnel metrics per project
- status: done
- spec: REQ-LNS-015, REQ-LNS-010
- owner: database-engineer
- depends: T-146, T-147
- write_scope: migrations/20261023_0015_core_lead_project_id.sql (new), services/core/app/leads.py (set the project on conversion), services/core/app/validation_worker.py (filters by project_id), services/core/app/repository.py (conversion write and the funnel reads), tests/unit/test_validation_worker.py, tests/unit/test_leads.py, tests/unit/test_sql_consistency.py, tests/unit/snapshots/table_catalog.json, tests/integration/test_sql_r1_flows.py (new cases only)
- context: services/core/app/validation_worker.py, services/core/app/leads.py, migrations/20261020_0009_validation_schema.sql, docs/runs/RUN-r1-i-validation.md, SPEC.md REQ-LNS-015
- size: L
- acceptance: `pytest -q tests/unit/test_validation_worker.py tests/unit/test_leads.py tests/unit/test_sql_consistency.py tests/unit/test_schema_catalog.py` and `python ops/check_migrations.py` and `make static` and `make test`
- note: decision D-26 (owner): the funnel metrics are per project. `validation.funnel_metric` already stores `project_id` but the worker computes by `service_line` because `core.lead` has no project. (i) Expand migration with the exact name above: `core.lead.project_id uuid NULL REFERENCES core.project(id)` and an index on `(tenant_id, project_id)`; it must be re-runnable and must not rewrite the table in a blocking way. (ii) The conversion of a lead to a project (`signal.converted_to_project`) fills it. (iii) The funnel queries of `validation_worker.py` filter by `project_id` instead of `service_line`; leads without a project are excluded from per-project metrics and counted in the service-line figure that stays for the tenant-wide row. Existing rows keep `project_id` null (no backfill guess). Contract steps (making it NOT NULL) are out of scope. Document in the report that until conversion fills the column the per-project funnel is empty for old leads.
- evidence: pytest validation_worker, leads, sql_consistency, schema_catalog 61 passed; check_migrations ok; make static ok; make test 1717 passed. Migration 0015 adds nullable core.lead.project_id (FK NOT VALID, partial index on (tenant_id, project_id)); insert_project in repository.py fills it on conversion; worker funnel reads filter by project_id and a tenant-wide funnel row counts all leads. Old leads keep project_id NULL, so the per-project funnel is empty for them until a conversion fills the column. Integration case added (not run, no Docker).

### T-182 Meta-ML metrics through events (deferred to R2)
- status: todo
- spec: REQ-LNS-016
- owner: lead-backend-engineer
- depends: T-147, T-174
- write_scope: services/ml/app/ (an emitter), services/pulse/app/ (an emitter), services/core/app/validation_worker.py (the aggregation), libs/contracts/events.py, migrations/ (one expand file with the next free number if a table is needed), tests
- context: services/core/app/validation_reads.py, services/ml/app/drift_worker.py, SPEC.md REQ-LNS-016 and D-25
- size: L
- acceptance: `pytest -q tests/unit/test_meta_ml_events.py` and `make static` and `make test`
- note: decision D-25 (owner), DEFERRED to R2: do not put this task in a wave until the owner opens R2. `psi`, `model_utility` and `decision_latency_days` cannot be read across schemas (INV-01). `ml` and `pulse` emit `drift_score`, `model_utility_run` and `decision_latency_event` through their outbox, and a `core` worker aggregates them into `validation.meta_ml_metric`. Until then those fields stay null with their current reason, which is correct. The event payloads need typed models like REQ-EVT-008 and an owner review of their fields before the work starts.
- evidence: filled by the agent when done

### T-183 Per-service token keys (planned L2)
- status: todo
- spec: REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-184
- write_scope: libs/common/internal_auth.py, libs/common/settings.py, docker-compose.yml, .env.example, ops/check_configuration.py, tests/unit/test_internal_auth.py, tests/unit/test_compose.py, docs/runbooks/go-live.md (one section)
- context: libs/common/internal_auth.py, services/core/app/main.py, docs/runs/RUN-r1-e.md (F-r1e-04), SPEC.md REQ-SEC-005 and D-28
- size: L
- acceptance: `pytest -q tests/unit/test_internal_auth.py tests/unit/test_compose.py tests/unit/test_gateway_routes.py` and `python ops/check_configuration.py` and `make static` and `make test`
- note: decision D-28 (owner): the shared HMAC key is accepted for now; per-service keys are planned L2 work, DEFERRED: do not put this task in a wave until the owner schedules it. One key per issuing service, rotatable (two keys valid during a rotation), the verifier picks the key by the service name in the token, and the shared key keeps working during the migration. Asymmetric tokens are rejected by the decision, do not propose them. Never print or log a key; the configuration check reports names only.
- evidence: filled by the agent when done

### T-184 Refuse the seed and verify_live callers in production
- status: done
- spec: REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-123
- write_scope: libs/common/internal_auth.py, libs/common/settings.py, tests/unit/test_internal_auth.py, ops/check_configuration.py, tests/unit/test_check_configuration.py
- context: libs/common/internal_auth.py, services/core/app/main.py (INGEST_WRITE, GATEWAY_OR_SEED, GATEWAY_OR_VERIFY), ops/bootstrap_roles.py (non production profile names), SPEC.md REQ-SEC-005 and D-28
- size: S
- acceptance: `pytest -q tests/unit/test_internal_auth.py tests/unit/test_check_configuration.py` and `python ops/check_configuration.py` and `make static`
- note: decision D-28 (owner) says the caller names `seed` and `verify_live` exist only outside production, but today the allow lists of core (`INGEST_WRITE`, `GATEWAY_OR_SEED`, `GATEWAY_OR_VERIFY`) accept them in any environment. Make `require_service_token` ignore those two names (answer 401 as for any unknown caller) when the environment is production, using the environment or profile setting that already exists (the non production names are local, test, ci and dev in `ops/bootstrap_roles.py`); do not add a new variable if one exists. Add a configuration check that fails when production is configured with the seed or verify credentials, and tests for both environments. The CI e2e and `make invent-tracker` run in a non production profile and must keep working.
- evidence: `pytest -q tests/unit/test_internal_auth.py tests/unit/test_check_configuration.py` 27 passed; `python ops/check_configuration.py` OK; `make static` and `make test` (1631 passed) green. Production = APP_PROFILE production or ENVIRONMENT outside local/test/ci/dev/development.

### T-185 The socioeconomic refresh workflow authenticates with a minted token
- status: done
- spec: REQ-SOC-001, REQ-SEC-005, REQ-DOC-001
- owner: second-backend-engineer
- depends: T-184
- write_scope: services/identity/iam/views.py, services/identity/iam/permissions.py, services/identity/iam/tokens.py, services/identity/project/urls.py, orchestration/n8n/wf_socioeco_refresh.json, orchestration/n8n/README.md, docs/runbooks/go-live.md (section 12 only), .env.example (names only), docker-compose.yml (the identity environment only), ops/check_configuration.py, tests/unit/test_identity_mint.py (new), tests/unit/test_n8n_workflows.py, tests/unit/test_runbook.py
- context: orchestration/n8n/wf_socioeco_refresh.json, libs/common/internal_auth.py, services/identity/iam/views.py, services/identity/iam/permissions.py, services/identity/iam/tokens.py, services/pulse/app/lens.py, docs/runbooks/go-live.md section 12, SPEC.md D-33
- size: M
- acceptance: `pytest -q tests/unit/test_identity_mint.py tests/unit/test_identity_rbac.py tests/unit/test_internal_auth.py tests/unit/test_n8n_workflows.py tests/unit/test_runbook.py && python ops/check_configuration.py && python ops/check_language.py`
- note: decision D-33 (owner, 2026-10-22), option B1. The problem is real: `POST /internal/identity/service-token` requires `IsInternalService` (a token already signed with the shared key), so n8n cannot obtain a first token. Resolution: (1) add a SEPARATE mint route in identity that accepts ONLY a dedicated, single-purpose credential, with its own secret distinct from `INTERNAL_SERVICE_TOKEN` (choose a variable name, add the NAME to `.env.example` with an empty value, compare in constant time, never log or print it). (2) That route can issue ONLY a token for the service and scope that `wf_socioeco_refresh` needs on the pulse refresh route (the service name is fixed on the server; the request body cannot choose it), with the normal 60 second TTL; it must not be able to mint a token for any other service, and a token minted by the shared key route must not be obtainable with it. Write an audit row per issue. (3) n8n holds only the dedicated credential; the first node of the workflow requests a token with it, the second calls `POST /internal/pulse/socioeconomic/refresh` with that token. (4) `INTERNAL_SERVICE_TOKEN` NEVER enters n8n, a workflow file or a credential, in any case (option B3 is forbidden). (5) Replace the credential list of section 12 of the go-live runbook (the coordinator already wrote the decision paragraph) and document the rotation of the dedicated credential. Tests required: the dedicated credential mints only the allowed token; a wrong or missing credential answers 401; it cannot mint another service; no response or log contains the secret. FALLBACK B2, only if the credential cannot be bounded without exposing the shared key: revert to a rotated static header accepted by pulse (`pulse-internal-header`) with a written rotation policy, REOPEN D-33 in SPEC.md with the note "reverted to a rotated static header because identity cannot issue a bounded mint credential without the shared key", and say so in the report. The workflow stays inactive (`active: false`) until one of the two works. Never write a credential value anywhere.
- evidence: `pytest -q tests/unit/test_identity_mint.py tests/unit/test_identity_rbac.py tests/unit/test_internal_auth.py tests/unit/test_n8n_workflows.py tests/unit/test_runbook.py && python ops/check_configuration.py && python ops/check_language.py` passed; `make static` passed; `make test` 1644 passed, 2 skipped. Option B1: route POST /internal/identity/mint/socioeco, variable N8N_SOCIOECO_MINT_CREDENTIAL, fixed caller n8n_socioeco, audit as an imt.audit log line (no identity audit table).

### T-186 Run verify-live inside the Compose network
- status: done
- spec: REQ-RUN-005, REQ-DOC-001
- owner: devops-engineer
- depends: T-076
- write_scope: docker-compose.yml (one new `verify-live` service under the profile `tools`, no `ports:`), Makefile (the `verify-live` target), ops/verify_live.py (only an in-network default mode if needed), .env.example (a comment on the overrides), docs/runbooks/go-live.md (section 7), tests/unit/test_compose.py, tests/unit/test_verify_live.py
- context: docs/runbooks/go-live.md, ops/verify_live.py, docs/runs/RUN-r1-d2-launch.md, docker-compose.yml, Makefile
- size: S
- acceptance: `pytest -q tests/unit/test_compose.py tests/unit/test_verify_live.py tests/unit/test_runbook.py && python ops/check_configuration.py`
- note: decision C1 of the owner (2026-10-22). `make verify-live` runs on the host and its defaults `http://localhost:8001` and `http://localhost:8002` do not answer because Compose does not publish the ingest and core ports. Do NOT publish them: `/internal` is not exposed, not even on the loopback. Instead run verify-live INSIDE the Compose network: add a service `verify-live` under the profile `tools` (same image and `.env` as the other Python services, `restart: "no"`) and make the Makefile target call `docker compose --profile tools run --rm verify-live python ops/verify_live.py ...`, reaching ingest and core by service name and container port (read the real ports from the `command:` lines of the ingest and core services in `docker-compose.yml`). Keep `VERIFY_LIVE_INGEST_URL` and `VERIFY_LIVE_CORE_URL` as overrides for whoever runs verify-live outside the network. Test that no `ports:` entry exists for ingest or core, that the new service has none, and that the target uses the profile. Remove the caveat paragraph of section 7 of the go-live runbook ONLY when the in-network run works (it cannot be proven without Docker: say so in the report and keep the paragraph if you cannot prove it, adding the new command next to it; T-187 confirms it on a machine with Docker). The earlier draft of this task (publish the ports on the loopback) is withdrawn.
- evidence: `pytest -q tests/unit/test_compose.py tests/unit/test_verify_live.py tests/unit/test_runbook.py` 78 passed; `python ops/check_configuration.py` OK; in-network run NOT proven (no Docker), runbook caveat kept, T-187 confirms

### T-187 Record the Docker integration and e2e run
- status: todo
- spec: REQ-QA-001, REQ-RUN-001
- owner: qa-reviewer
- depends: T-171
- write_scope: docs/runs/RUN-golive-docker.md (the coordinator created the skeleton marked PENDING)
- context: docs/runbooks/go-live.md, docs/runs/RUN-r1-e.md (F-r1e-10), Makefile
- size: S
- acceptance: `test -f docs/runs/RUN-golive-docker.md && ! grep -q PENDING docs/runs/RUN-golive-docker.md`
- note: decision D-32 (owner): the integration tier and the e2e are run by the OWNER (or qa-reviewer) on a machine with Docker, with `make invent-tracker`, before go-live. This task is a record only and is NOT launched by an agent: the owner runs `make test-integration`, `make invent-tracker`, `make doctor` and `python ops/e2e_check.py`, then writes one line per command with the result in `docs/runs/RUN-golive-docker.md`, plus every item the earlier reports marked unverified for lack of Docker. It is a high priority prerequisite of go-live.
- evidence: filled by the owner when done

### T-188 Production callers send the parent event ids
- status: blocked (partially done)
- spec: REQ-LNS-012, REQ-EVT-008
- owner: lead-backend-engineer
- depends: T-175, T-176, T-181
- write_scope: services/core/app/funnel.py, services/core/app/leads.py, services/core/app/imports.py, services/core/app/main.py (the lead create call only), services/ml/app/backfill.py, tests/unit/test_funnel.py, tests/unit/test_event_chain_producers.py, tests/unit/test_ml_backfill.py, docs/runbooks/event-chain-rollout.md (section 6 only)
- context: docs/runs/RUN-r1-m-events.md, libs/domain/event_chain.py, services/core/app/funnel.py, services/ml/app/backfill.py, SPEC.md REQ-LNS-012
- size: M
- acceptance: `pytest -q tests/unit/test_funnel.py tests/unit/test_event_chain_producers.py tests/unit/test_ml_backfill.py tests/unit/test_event_chain.py` and `make static` and `make test`
- note: T-175 added the optional fields (`ScoreFeatures.parent_event_id`, `LeadCreate.scored_event_id`, `LeadStatusPatch.gate_event_id`) but three production callers still do not fill them, so `trace_chain` keeps reporting gaps and falls back to `reference_parent`. Close them: (1) `funnel.py` exposes the id of the gate decision event it writes and passes it as `parent_event` to the pipeline stage producer; (2) `services/ml/app/backfill.py` sends the `parent_event_id` it knows (the id of the `signal.tagged` event for the company, read through core's internal features route, never from a shared table) and leaves it empty when none exists; (3) the lead create calls in `main.py` and `imports.py` pass `scored_event_id` when the company has a stored score event. Each field stays optional, so a missing id never fails a request. Add an offline test per caller that asserts the id reaches the payload, and update section 6 of the event chain runbook (the known gaps that are now closed). Do not change any response model beyond what T-175 added. Run after T-181 in the same session because both touch `leads.py` and `repository.py`.
- evidence: done in scope: funnel.py on_gate_event hook (gate event id exposed, response unchanged), backfill.py forwards parent_event_id when the features payload carries one, offline tests in test_funnel.py and test_ml_backfill.py, runbook section 6 updated. Blocked: (a) core features route and ml core_features.py do not carry the signal.tagged id (files outside scope); (b) core stores no signal.scored id (needs migration, worker handler, repository read), so main.py and imports.py lead create cannot pass scored_event_id; (c) no route links a gate decision to a stage change. Follow-up task needed for (a) and (b).

### T-189 The pulse socioeconomic refresh accepts only its own caller
- status: done
- spec: REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-185
- write_scope: services/pulse/app/lens.py (the dependency of the refresh route only), tests/unit/test_pulse_lens.py, tests/unit/test_internal_auth.py (one case)
- context: docs/runs/RUN-r1-m-auth.md, libs/common/internal_auth.py, services/pulse/app/lens.py
- size: S
- acceptance: `pytest -q tests/unit/test_pulse_lens.py tests/unit/test_internal_auth.py` and `make static` and `make test`
- note: T-185 mints a 60 second token for the fixed caller `n8n_socioeco`, but the pulse refresh route still uses `require_internal_token`, which accepts a validly signed token from any service name. Replace that dependency on the refresh route with `require_service_token("n8n_socioeco")` and keep the other pulse routes as they are. Tests: the `n8n_socioeco` token is accepted; the `gateway`, `seed` and `verify_live` tokens and a missing token answer 401; a token signed with another key answers 401. Do not change the mint route or the workflow, and do not activate the workflow.
- evidence: pytest -q tests/unit/test_pulse_lens.py tests/unit/test_internal_auth.py (plus test_pulse_lens_routes.py, test_openapi.py) passed; make static OK; make test 1689 passed, 2 skipped, 4 xfailed. Note: test_pulse_lens.py is new (the file named in the task did not exist); the refresh cases of test_pulse_lens_routes.py now send the n8n_socioeco token

### T-190 Bring the test lint to zero
- status: done
- spec: REQ-QA-001
- owner: coordinator
- depends: T-172
- write_scope: tests/unit/test_attribution.py, tests/unit/test_connectors.py, tests/unit/test_connectors_more.py, tests/unit/test_funnel.py (lint fixes only, the T-188 agent edits the same file in another session: keep your edits to the flagged lines), tests/unit/test_optional_extras.py, tests/unit/test_profiling.py, tests/unit/test_unique_indexes.py, tests/unit/test_workflows.py, tests/unit/test_event_chain_producers.py, tests/unit/test_score_event.py, docs/runs/
- context: docs/runs/RUN-r1-l-platform.md (the list of remaining findings)
- size: S
- acceptance: `ruff check tests` reports no finding, and `make static` and `make test` pass
- note: T-172 left the findings of `ruff check tests` that `make static` does not cover (B905, E501, F401, B023, I001 in the files above). Fix them without changing what a test asserts; if a fix would change behaviour, add a narrow `# noqa` with the reason instead. Then list in the report the exact command and its result. Do NOT change the Makefile or the CI workflow: whether `ruff check tests` joins `make static` is a decision for the owner, so state your recommendation in the report.
- evidence: `ruff check tests` -> All checks passed (was 14 findings); make static OK; make test 1689 passed, 2 skipped, 4 xfailed

### T-191 Compliance routes become tenant-aware
- status: todo
- spec: REQ-CMP-001, REQ-SEC-005, REQ-DAT-005
- owner: lead-backend-engineer
- depends: T-082, T-194
- write_scope: services/core/app/main.py (the two compliance routes: opt-out requires `X-Tenant-Id`, purge runs per tenant), services/core/app/retention.py (opt_out and purge set the tenant context; the CLI iterates the tenants), services/core/app/repository.py (the retention unit of work applies the context), services/core/app/identity_tenants.py (new: the client that lists tenants from identity), tests/unit/test_retention.py, tests/integration/test_sql_r1_flows.py (new cases only), docs/compliance/processing-activities.md, docs/runbooks/go-live.md (one line)
- context: SPEC.md decision D-36 and REQ-CMP-001, docs/runs/RUN-r1-n-rls.md (risks), services/core/app/retention.py, services/core/app/repository.py
- size: M
- acceptance: `pytest -q tests/unit/test_retention.py` and a real Postgres case as `core_app` with two tenants where the opt-out of tenant A deletes only its rows and the retention job purges each tenant separately, plus `make static` and `make test`
- note: decision D-36 (owner), option A. Since T-082 the opt-out, the purge route and `python -m services.core.app.retention` run without a tenant context and delete nothing. Make them tenant-aware: (1) `POST /internal/core/compliance/opt-out` requires `X-Tenant-Id` (answers 400 without it) and calls `set_tenant_context` before the first statement; a company of another tenant is `not_found`, never deleted; opt-out by NIT works only inside the tenant of the caller, the same company in two tenants needs two requests; (2) the retention job and the purge route get the tenant ids from the identity API (T-194, service token, allow list), never from `core.tenant_config` (under RLS), and set the context for each tenant before purging it; one tenant that fails does not stop the others and the exit code reports it; (3) no new role and no policy that crosses tenants (D-29, REQ-SEC-005); (4) the audit entry keeps only the hashed identifiers (`retention._fingerprint`) plus action, requested_via, counts per table, timestamp and tenant_id, never plain PII. The gateway has NO compliance route today (checked on main after PR #80): the callers are the seed and internal callers. Do not add a public gateway route here; if the owner wants one it is a separate API decision, so say it in the report. Add a test that fails when a purge deletes zero rows for a company that exists.
- evidence: filled by the agent when done

### T-192 Check for rows without a tenant before migration 0014
- status: todo
- spec: REQ-DAT-005, REQ-RUN-001
- owner: database-engineer
- depends: T-082
- write_scope: ops/doctor.py, tests/unit/test_doctor.py, docs/runbooks/go-live.md (one paragraph in the migration section)
- context: migrations/20261023_0014_core_rls_signal_location_outcome.sql, docs/runs/RUN-r1-n-rls.md, ops/doctor.py, ops/e2e_check.py
- size: S
- acceptance: `pytest -q tests/unit/test_doctor.py tests/unit/test_runbook.py && python ops/check_language.py`
- note: migration 20261023_0014 raises an exception, and the `migrate` container stops, when `core.signal`, `core.location` or `core.lead_outcome` has a row whose tenant cannot be resolved from its company or lead. A fresh stack is unaffected, but a database that held F0 data is not. Add `ops/doctor.py --tenants`, read only and counts only, through the same superuser psql path as `--pipeline`: rows with a NULL `tenant_id` and rows that would stay unresolved (NULL `tenant_id` whose company or lead also has a NULL tenant) for those three tables plus `core.company` and `core.lead`. Exit 1 when any count is above zero. Document in the go-live runbook that this command runs BEFORE the first migrate of an existing database, and what the operator does with the result (assign a tenant, never delete). Never print rows or ids.
- evidence: filled by the agent when done

### T-193 Pin the owner role of verify-live under row level security
- status: todo
- spec: REQ-SEC-005, REQ-RUN-006
- owner: devops-engineer
- depends: T-186
- write_scope: ops/verify_live.py, tests/unit/test_verify_live.py, tests/unit/test_compose.py (one case), docs/runbooks/go-live.md (section 7, one sentence)
- context: ops/verify_live.py, docker-compose.yml (service verify-live), docs/runs/RUN-r1-n-rls.md (risks)
- size: S
- acceptance: `pytest -q tests/unit/test_verify_live.py tests/unit/test_compose.py tests/unit/test_runbook.py`
- note: `ops/verify_live.py` counts `core.signal` directly (an operator tool, INV-01 does not apply). With T-082 that table is under forced row level security, so a connection as `core_app` would count 0 and the signal hop would fail with a misleading message. Today the default URL and the Compose service use `POSTGRES_USER` (the owner), which is correct. Pin it: a test that the compose `verify-live` URL never uses an `_app` role, a refusal in `ops/verify_live.py` (exit 2 with a clear message, no value printed) when `VERIFY_LIVE_DATABASE_URL` names a role ending in `_app`, and one sentence in section 7 of the go-live runbook.
- evidence: filled by the agent when done

### T-194 Identity lists the tenants for service callers
- status: todo
- spec: REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-185
- write_scope: services/identity/iam/views.py, services/identity/iam/permissions.py, services/identity/project/urls.py, tests/unit/test_identity_tenants.py (new), tests/unit/test_openapi.py (NOT_PUBLIC entry only), docs/runs/
- context: SPEC.md decision D-36, services/identity/iam/models.py (Tenant), services/identity/iam/permissions.py (InternalServiceToken), docs/runs/RUN-r1-m-auth.md
- size: S
- acceptance: `pytest -q tests/unit/test_identity_tenants.py tests/unit/test_openapi.py` and `make static` and `make test`
- note: the retention job needs the list of tenants and must not read `core.tenant_config` (row level security) nor another schema (INV-01). Add `GET /internal/identity/tenants`, read only, protected by a signed service token with an allow list that contains only `core`; it returns the tenant ids and nothing else (no names, no members, no secrets) and only active tenants. Tests: accepted for `core`; 401 for gateway, seed, verify_live and a missing token; the body has only ids. Not public (NOT_PUBLIC in `test_openapi.py`).
- evidence: filled by the agent when done

### T-195 Per-tenant unique key on core.signal (expand)
- status: blocked
- spec: REQ-DAT-005, REQ-SEC-005, REQ-ING-009
- owner: database-engineer
- depends: T-082
- write_scope: migrations/20261024_0017_core_signal_tenant_unique.sql (new), services/core/app/repository.py (create_signal only), services/ingest/app/ (only if it builds `source_external_id` or has its own upsert), tests/unit/test_sql_consistency.py, tests/unit/snapshots/table_catalog.json (only if the catalog test requires it), tests/integration/test_sql_r1_flows.py (new cases only), docs/runs/
- context: SPEC.md decision D-37, migrations/20261017_0001_expand_initial_schemas.sql (the global UNIQUE), services/core/app/repository.py (create_signal), docs/runs/RUN-r1-n-rls.md
- size: M
- acceptance: the integration case with two tenants and the SAME `(source, source_external_id)` passes (each tenant gets its own row, neither sees the other's, `create_signal` does not raise), `python ops/check_migrations.py`, `pytest -q tests/unit/test_sql_consistency.py`, `make static` and `make test`
- note: decision D-37 (owner), option B, priority: do it before a second tenant is onboarded. Today the global `UNIQUE (source, source_external_id)` makes `ON CONFLICT DO NOTHING` hit the row of another tenant, the fallback `SELECT ... scalar_one()` is hidden by row level security and raises, so `create_signal` fails. Expand only (INV-06), in ONE pull request: (1) the migration creates `UNIQUE (tenant_id, source, source_external_id)` (a unique index, `IF NOT EXISTS`, re-runnable) and FIRST verifies that no row violates it, failing with an explicit message and never deduplicating by deleting; the global unique stays until T-196; (2) `create_signal` uses `ON CONFLICT (tenant_id, source, source_external_id)` and the fallback SELECT filters by `tenant_id`; (3) the integration test above. The migration number is pinned to 0017 (0014 is T-082, 0015 is T-181, 0016 is the ml outbox).
- evidence: BLOCKED (run r1-o-schema). The acceptance case (two tenants, same `(source, source_external_id)`, each gets its own row, `create_signal` does not raise) cannot pass while the global `UNIQUE (source, source_external_id)` stays, which the note requires until T-196. `ON CONFLICT (tenant_id, source, source_external_id)` only absorbs a violation of that one index; the insert of the second tenant still violates the global unique and raises. See docs/runs/RUN-r1-o-schema.md for options.

### T-196 Drop the global unique key on core.signal (contract)
- status: todo
- spec: REQ-DAT-005, REQ-ING-009
- owner: database-engineer
- depends: T-195
- write_scope: migrations/contract_20261024_0018_drop_signal_global_unique.sql (new), tests/integration/test_sql_postgres.py (only if an assertion lists the old key), docs/runs/
- context: SPEC.md decision D-37, docs/runs/ (the T-195 report), ops/check_migrations.py
- size: S
- acceptance: `python ops/check_migrations.py --base origin/main` and `pytest -q tests/unit/test_sql_consistency.py` and `make static` and `make test`
- note: INV-06: a contract file goes in its own pull request and only after the code of T-195 is on main and no statement depends on the old key. Drop the global `UNIQUE (source, source_external_id)` of `core.signal` (look up the real constraint name in the migrations; do not guess it). Do not launch this task in the same wave as T-195, and not before T-195 is merged AND its migration is applied in the target environment (the code must already use the per-tenant key before the global one is dropped); when in doubt that it is applied, wait.
- evidence: filled by the agent when done

### T-197 Gateway route for operator-initiated opt-out (admin role)
- status: todo
- spec: REQ-CMP-001, REQ-SEC-005
- owner: lead-backend-engineer
- depends: T-191
- write_scope: services/gateway/app/registry.py, services/gateway/app/routes.py, docs/api/openapi.json, tests/unit/snapshots/, tests/unit/test_gateway_routes.py, tests/unit/test_openapi.py, docs/compliance/processing-activities.md (one line), docs/runbooks/go-live.md (one line)
- context: SPEC.md decision D-36 and REQ-CMP-001, services/gateway/app/auth.py (the tenant is derived from the JWT), services/gateway/app/registry.py, docs/runs/RUN-r1-decisions-d36-d37.md
- size: S
- acceptance: `pytest -q tests/unit/test_gateway_routes.py tests/unit/test_openapi.py tests/unit/test_contract_snapshot.py` and `make static` and `make test`
- note: tracked by decision D-36 (owner). For the go-live of run 1 the internal path of T-191 is enough: the retention job covers the age based purge and the operator runs a one-off opt-out through the seed. This task is the way a data subject's deletion request is processed from the app by an admin or legal user, and it is required BEFORE go-live if legal requests are processed by users. Do not launch it before T-191. Map `POST /api/v1/compliance/opt-out` in the gateway for the admin role only; the gateway injects `X-Tenant-Id` from the JWT as it does on the other routes (never from the request body) and forwards to `POST /internal/core/compliance/opt-out`. Purge stays internal. The change must be additive for the OpenAPI diff; regenerate `docs/api/openapi.json` and the snapshots. Tests: admin allowed; every other role 403; a body that names another tenant does not change the tenant sent to core.
- evidence: filled by the agent when done
