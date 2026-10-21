# Run report r1-e

Branch: `agent/r1-e-security`. Tasks: T-123 (done, landed as commit e5f5e28), T-080 (this review pack, produced by the architecture-reviewer).
Reviewer notes and raw command logs: `/tmp/agent-arch/` (not tracked).

## Run 1 review pack (T-080)

### 1. Acceptance commands (sandbox: Python 3.12 venv, Docker CLI 29.9.1 present, no daemon)

| Command | Result | Evidence |
| --- | --- | --- |
| `make static` | pass (exit 0) | ruff, format, mypy, guards |
| `make test` | pass | 1151 passed, 2 skipped, 16 deselected (integration tier), 5 xfailed |
| `make coverage` | pass | total 95.53 percent (gate 80); `libs/domain` 99 percent (gate 90) |
| `make openapi` | pass | no tracked file changed (`git status` clean afterwards), so nothing to revert |
| `python ops/check_traceability.py` | pass | 119 requirements, 109 tasks; notes only for REQ-PRD-015 and REQ-PRD-016 (later-run, uncited) |
| `make compose-config` | pass (exit 0) | `docker compose config` works offline; this only validates syntax and interpolation |

All acceptance commands passed. Deselected integration tests (16) and the 2 skips need Postgres/Docker and are unverifiable here.

### 2. ADR-IMT-0001 section E2.5 checklist

| # | Item | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | Migrations create exactly 7 schemas and 14 roles, same names everywhere | partial / fail | `ops/schemas.py` SCHEMAS has 7 and drives `ops/bootstrap_roles.py` (14 roles, 7 owner plus 7 app). But `migrations/20261017_0001_expand_initial_schemas.sql` lines 13 and 15 still create `analytics` and `socioeconomic`, tolerated through `LEGACY_SCHEMAS`; no migration ever drops `analytics` (0002 line 685 says "retire only after parity is verified"). So a migrated database holds 8 to 9 schemas. Role names also drift: compose `bootstrap-roles` passes passwords only for ingest, core, ml, pulse, brief (also `.env.example` lines 117-121); `identity_app` and `socio_app` get the default `CHANGE_ME_IDENTITY` and `CHANGE_ME_SOCIO` login passwords (`ops/bootstrap_roles.py` line 56). Live role creation is unverifiable (no Postgres). |
| 2 | `socioeconomic` retired (`contract_` migration, separate commit) | partial | `migrations/contract_20261018_0003_retire_socioeconomic.sql` exists and `ops/migrate.py` orders it by version. Not verifiable that it was committed apart from code, and unverifiable that it runs on a real DB. Stale code and docs still say socioeconomic: `orchestration/n8n/wf_socioeco_refresh.json` line 38 and `orchestration/n8n/README.md` line 24 call `{ANALYTICS_URL}/internal/analytics/socioeconomic/refresh`; `tests/unit/test_compose.py` line 171 asserts `ANALYTICS_URL` is absent from compose, so this workflow is broken at runtime. |
| 3 | `services/analytics` merged into `services/pulse`; routes and env vars renamed | partial | Directory absent and pulse serves the routes (`services/pulse/app/main.py`). Leftovers: `.codex/agent-registry.yaml` still lists `legacy_paths: services/analytics/` (REQ-ARC-001 says remove when deleted); docstrings in `libs/common/redis_streams.py`, `redis_client.py`, `libs/contracts/events.py`, `services/core/app/outbox_relay.py` mention analytics; the n8n workflow above. |
| 4 | `brief` tables are in schema `brief`, not `core` | pass (static) | `migrations/20261017_0002_arch_v22_foundations.sql` plus `20261019_0007_brief_config_version.sql`; `tests/unit/test_brief_schema.py`, `test_schema_catalog.py`. Live DB unverifiable. |
| 5 | Each consumer has its `processed_event` | pass (static) | core, ingest (0004), pulse, brief, ml in migrations; legacy `analytics.processed_event` still present (see item 1). `tests/unit/test_sql_consistency.py`. |
| 6 | `docker-compose.yml`, README, docs say 7 services plus streamlit | fail | Compose lists gateway, ingest, core, ml, pulse, brief, identity, streamlit plus workers: pass. `README.md` line 8 says "five Python services", "Postgres with five schemas", and line 16 lists "gateway, ingest, core, analytics, ml, and mocks": fail. |
| 7 | Agent registry gives owners to identity, brief, pulse, streamlit | pass | `.codex/agent-registry.yaml` lines 56, 57, 65, 67; `tests/unit/test_registry.py`. |
| 8 | Event catalog meets the E2.3 extension contract | pass (static) | REQ-EXT-001 to 004 are covered by tasks and tests (`test_registry`, `test_schema_catalog`, tenant config tests); traceability OK. |

### 3. SPEC section 6, L1 definition of done

| Item | Verdict | Evidence |
| --- | --- | --- |
| Every L1 run1 requirement done with evidence or blocked with a reason | pass | 94 of 96 non-L2 tasks `done`, none `blocked`; only `T-000` (smoke test, `todo`, never recorded) and `T-080` remain. 119 requirements traced. Many SPEC requirements still show `tasks: none yet` in their text; the guard accepts it, see F-r1e-09 |
| `make static`, `make test` pass; `compose-config` where Docker CLI exists; 80 and 90 percent coverage | pass | section 1 |
| Offline pipeline test (REQ-QA-004) inside `make test` | pass | `tests/unit/test_pipeline_flow.py` in the 1151 passed |
| Repository conforms to E2.5 | fail | section 2: items 1, 2, 3, 6 |
| All `REQ-EXT-*` pass | pass (static) | section 2 item 8 |
| Owner runs `make invent-tracker` and reads seeded data through the gateway | unverifiable in sandbox | target exists (Makefile line 21); needs Docker daemon. Compose risk: the `identity` service has no `POSTGRES_HOST`, `IDENTITY_DB_PASSWORD` or `IDENTITY_DB_USER` in compose or `.env.example` (`services/identity/project/settings.py` lines 58-66 default to host `localhost` and an empty password), so identity probably cannot reach Postgres in the compose stack, and login would fail. See F-r1e-02. |
| `make doctor` and `make verify-live` with live credentials | unverifiable in sandbox | targets exist (Makefile lines 49, 52); runbook `docs/runbooks/go-live.md`; no Bright Data or RUES here |
| `docs/api/openapi.json` current | pass | `make openapi` left the tree clean |
| Run report lists done, blocked, commands, assumptions, unverified mappings, files, risks | pass with this file | sections 1 to 8 |

### 4. Unverified field mappings (D-13)

All five source mappings carry `unverified: true` and none has a recorded provider sample (`tests/fixtures/brightdata/` does not exist; synthetic fixtures do not count):

- `libs/domain/source_mappings/linkedin_companies.yaml`
- `libs/domain/source_mappings/linkedin_jobs.yaml`
- `libs/domain/source_mappings/instagram.yaml`
- `libs/domain/source_mappings/maps.yaml`
- `libs/domain/source_mappings/computrabajo.yaml` (also the dataset id `computrabajo-tech-co` is a guess, RUN-r1-b-b)

Field names come from public Bright Data documentation. Closing path: owner supplies dataset IDs, `make verify-live` captures redacted records, `ops/verify_mappings.py` passes on them (T-096). The RUES lookup flow is also unverified against the real service.

### 5. Blocked or todo tasks that T-080 depends on

None. All 28 dependencies (T-064, T-065, T-077 to T-079, T-081, T-083 to T-089, T-092 to T-098, T-120 to T-127) are `done` in `TASKS.md`. Outside the dependency list: `T-000` is `todo` (smoke report never written; low value) and `T-082` (row level security) is deliberately unassigned; RLS_EXEMPT on 9 tenant_id tables is pending an owner decision (evidence line of T-094/T-095).

### 6. T-123 review against REQ-SEC-005

Statement: each service uses its own `_app` role; service-to-service calls carry a signed token; Redis has ACL users `edge` and `data`; `/internal/*` is never routed by the gateway.

| Clause | Verdict | Evidence |
| --- | --- | --- |
| Signed service token on all calls | pass | `libs/common/internal_auth.py`: `v1.<service>.<exp>.<hmac>`, 60 s TTL, constant-time compare; legacy raw secret removed from `require_internal_token`. Senders: gateway (`services/gateway/app/main.py` lines 204, 331), brief (`core_source.py` line 54), ingest (`to_core.py` line 109), `ops/seed/loader.py`, `ops/verify_live.py`. 36 tests in `test_internal_auth.py` and `test_service_clients.py`. |
| Core per-route allow lists | pass | `services/core/app/main.py` lines 80-84: gateway only; gateway, ingest, seed for writes; gateway, brief for brief reads; seed and verify_live variants. |
| Per-service `_app` role | partial | Compose uses `ingest_app`, `core_app`, `ml_app`, `pulse_app`, `brief_app`; `identity` and `socio` are not wired (F-r1e-02). Core, ingest, pulse, ml workers reuse their service role: fine. |
| Redis ACL users `edge` and `data` | fail (not implemented) | `docker-compose.yml` redis service (line 89) is a plain `redis:7-alpine`; every service uses `redis://redis:6379/0` with no user; no ACL file. `check_configuration.py` (T-123 note asked to record this) only checks that `INTERNAL_SERVICE_TOKEN` is set per service. |
| `/internal/*` never routed by the gateway | pass for external clients | gateway builds internal URLs only from its own route table (`services/gateway/app/routes.py`). Not re-verified by a live request. |

Further observations:

- One shared HMAC key (`INTERNAL_SERVICE_TOKEN`) for every service: the service name in the token is a claim, not proof. A compromised pulse or ml container can mint `gateway` tokens and reach every core route and tenant header. The allow lists limit accidents, not an attacker. Per-service keys or asymmetric signatures would give real zero trust.
- Only core pins callers. identity, brief, ingest, pulse, ml and `/internal/metrics` use `require_internal_token` and accept a token from any service name.
- `seed` and `verify_live` are accepted caller names in production core; they hold the production secret when run by an operator. Disable them when `ENVIRONMENT` is production, or document it.
- Tokens have no nonce, so replay is possible within 60 s; acceptable inside a private network, note for the threat model.
- `docs/` and `SPEC.md` still describe legacy mode, and the REQ-SEC-005 `tasks:` line says "none yet". The coordinator owns SPEC and the ADRs are not editable.
- CI end to end with tokens cannot be run here (no Docker); unit coverage only.

Overall: REQ-SEC-005 is about two thirds implemented. Do not mark it fully done until Redis ACLs, identity and socio roles are handled or an owner decision relaxes the requirement.

### 7. Assumptions

- Offline tests with fakes stand for the live behavior of Postgres, Redis, Supabase, Bright Data, RUES; the integration tier (16 tests) did not run.
- `docker compose config` success validates syntax only, not that containers start.
- E2.5 verdicts on migrations were taken from reading the SQL and the offline SQL checks, not from a migrated database.
- The unreviewed `frontend/` and L2/L3 work are out of scope.
- Traceability notes for REQ-PRD-015 and REQ-PRD-016 are accepted as later-run.

### 8. Risks

1. Fresh databases hold legacy `analytics` and `socioeconomic` schemas (or double data) until a contract migration runs; the n8n socio refresh calls a URL that no longer exists.
2. Login roles `identity_app` and `socio_app` carry a known default password in any environment bootstrapped from compose.
3. Identity service likely cannot start against the compose Postgres, which breaks login in `make invent-tracker`.
4. Shared service token and an open Redis weaken the zero-trust claim.
5. All five field mappings are guesses until a real snapshot is captured.
6. RLS exemptions on 9 tables (T-082) are undecided.
7. Documentation drift (README, registry legacy path, docstrings, TASKS wording).

### 9. Follow-up items

| Id | Finding | Suggested owner | Severity |
| --- | --- | --- | --- |
| F-r1e-01 | Add a `contract_` migration that moves any `analytics` data to `pulse` and drops `analytics`; stop creating legacy schemas on fresh DBs only via a new baseline if the owner agrees; update `LEGACY_SCHEMAS` afterwards (E2.5 items 1 and 2). Separate commit from code. | lead-backend-engineer | high |
| F-r1e-02 | Wire identity into compose: `POSTGRES_HOST`, `IDENTITY_DB_USER`, `IDENTITY_DB_PASSWORD`, add `BOOTSTRAP_PASSWORD_IDENTITY` and `BOOTSTRAP_PASSWORD_SOCIO` to the `bootstrap-roles` service and `.env.example`; make bootstrap refuse default passwords outside the local profile; extend `check_configuration.py` and `test_compose.py`. | lead-backend-engineer | high |
| F-r1e-03 | Implement Redis ACL users `edge` (rate-limit keys only) and `data` (streams, cache) in compose and settings; add a configuration check (REQ-SEC-005). | lead-backend-engineer | high |
| F-r1e-04 | Replace the shared HMAC key with per-service keys (or asymmetric tokens) and make pulse, ml, ingest, brief, identity pin the gateway or an allow list; limit `seed` and `verify_live` caller names to non-production. | lead-backend-engineer | medium |
| F-r1e-05 | Fix `orchestration/n8n/wf_socioeco_refresh.json` and its README: use `PULSE_URL` and the pulse socio refresh route; add a test that no workflow references removed variables. | second-backend-engineer | medium |
| F-r1e-06 | Update `README.md` to 7 services plus Streamlit, 7 schemas, `pulse` instead of `analytics` (E2.5 item 6). | coordinator | medium |
| F-r1e-07 | Remove `legacy_paths: services/analytics/` from `.codex/agent-registry.yaml` (and the profile sync) and rename analytics mentions in docstrings of `libs/common` and `libs/contracts`, `services/core/app`. | coordinator (registry), lead-backend-engineer (docstrings) | low |
| F-r1e-08 | Capture redacted real snapshots with `make verify-live`, store under `tests/fixtures/brightdata/`, run `ops/verify_mappings.py`, flip `unverified` per source (D-13); confirm the Computrabajo dataset id. | owner with second-backend-engineer | medium |
| F-r1e-09 | Fill the `tasks:` lines of SPEC requirements that still say "none yet" or record why; update the REQ-SEC-005 text once F-r1e-03 and F-r1e-04 are decided. | coordinator | low |
| F-r1e-10 | Run the integration tier and the CI e2e (T-064, T-078) on a machine with Docker, including `make invent-tracker`, and record the result; this covers everything marked unverifiable above. | owner / qa-reviewer | high |
| F-r1e-11 | Decide the RLS exemptions on 9 tenant_id tables (T-082) and record the decision. | owner | medium |
| F-r1e-12 | Close or delete `T-000` (smoke report) so no L1 task stays `todo`. | coordinator | low |

### 10. Run summary

Tasks done: T-123 (earlier commit). T-080 produced by this pack; its status and evidence are for the coordinator to set. Blocked: none. Layer L2 tasks (T-100 to T-112) have not run and remain `todo`. Changed files in this task: `docs/runs/RUN-r1-e.md` only; no code, no TASKS.md edit.
