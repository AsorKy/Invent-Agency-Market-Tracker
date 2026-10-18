# Run report r1-a

Branch `agent/r1-a`. Scope: T-003 to T-018 (T-000 to T-002 were already done). One commit per task, pushed after each.

## Tasks

Done (16): T-003, T-004, T-005, T-006, T-008, T-009, T-010, T-011, T-012, T-013, T-014, T-015, T-016, T-017, T-018.

Blocked (1): T-007 (CI unit job). The change is finished and verified as YAML, but the push token has no `workflow` scope, so GitHub rejects any commit that touches `.github/workflows/`. The `contents` API also returned 403. The diff is in `docs/runs/T-007-ci-unit-job.patch`. The owner applies it with `git apply docs/runs/T-007-ci-unit-job.patch`, then removes the patch file.

Dependency order used: T-003/T-008/T-009 -> T-004/T-010/T-011 -> T-012 -> T-005/T-006/T-013 -> T-016/T-018 -> T-007/T-017 -> T-014. T-014 had T-011, T-015 and T-018 done, so it was not skipped.

## Commands and results

- Sandbox is offline. `pip install` fails, so pydantic, fastapi, httpx, redis, fakeredis and sqlalchemy are missing. The default interpreter is Python 3.11, which cannot parse the PEP 695 syntax in `libs/common/retry.py`.
- Tests ran with `/tmp/py12` (python3.12 plus the pytest from the uv tool environment): `-m pytest -q` -> 137 passed, 14 skipped, 1 xfailed.
- `ruff check` and `ruff format --check` on libs, services, datagen, ops and tests: clean.
- `python ops/check_migrations.py`, `check_restricted_assets.py`, `check_configuration.py` (82 variables), `check_language.py`, `check_traceability.py` (119 requirements, 83 tasks), `check_workflows.py`: all OK.
- `mypy ops`: clean. Full `make static`: not run end to end. `check_script_docs.py` and the mypy run over libs fail here only because of the PEP 695 parse problem on 3.11 (pre-existing).

## Not verified offline (skipped here, written from reading the code)

These skip cleanly via `importorskip` and must be run in CI: `test_gateway.py` (about 45 cases), `test_outbox.py`, the endpoint cases of `test_ingest_webhook.py`, `test_health.py`, `test_settings.py`, `test_db_settings.py`, the pydantic cases of `test_contracts.py` and `test_contract_snapshot.py`. The new SQL in migration 0004 was only checked by the guard and text parsing, never applied to Postgres.

## Assumptions

- `source_version` on the event envelope is a string (ADR E2.3 was not checked).
- Datagen snapshot field names for maps and computrabajo are plausible Bright Data names, not verified against real samples (`libs/domain/source_mappings` does not exist yet). All such mappings are `unverified` (D-13).
- Live mode requires `BRIGHT_DATA_API_TOKEN`, `BRIGHT_DATA_WEBHOOK_SECRET`, and a dataset ID for every enabled source. Errors name variables only. Empty optional variables count as unset.
- `ops/__init__.py` was added by the coordinator so mypy resolves `ops.schemas` once.
- AGENTS.md roster was updated by the coordinator to match the registry (T-016).

## Conflicts and decisions for the owner

1. Source conflict: REQ-DAT-001 says `analytics` is never created, but applied migration 0001 creates it (and `socioeconomic`). Applied migrations were not edited. `contract_20261018_0003_retire_socioeconomic.sql` retires `socioeconomic`; `analytics` stays until `services/analytics` is merged into pulse. A strict xfail in `tests/unit/test_schema_catalog.py` flips to failure once resolved, which forces a decision.
2. The contract file number 0003 sorts after 0004 because `c` sorts after digits, so the drop always runs last. This is correct; renaming it to 0005 is optional.
3. Ingest tables have no `tenant_id`, so no RLS applies to them.
4. 0001 leaves `core.brief` in core, and 0002 adds `core.import_job`, `import_row`, `idempotency_key`. These are not in the SPEC catalog and are tolerated.

## Risks

- `tests/unit/snapshots/model_schemas.json` is not generated. In CI with pydantic, `test_pydantic_model_schemas_frozen` fails by design until someone runs `UPDATE_SNAPSHOTS=1 pytest tests/unit/test_contract_snapshot.py` and commits the file. Review the other snapshot diffs first.
- Production findings to hand to lane owners: `identity`, `streamlit` and `mocks` lack a real `/ready` (xfail in test_health); the ingest webhook parses the body before it checks the signature, so an unsigned invalid body gets 422 instead of 401; `redact_log_record` matches only exact lowercase keys such as `password`/`api_key` variants; `services/pulse` has no consumer yet (the consumer is in `services/analytics`); `relay_once` builds a new engine and Redis client per call; the ml consumer logic is inline in `main` and not testable on its own.
- The supervisor (T-012) exists but nothing calls it yet; compose or Dockerfile wiring belongs to a later task.
- The secret scan (T-017) allowlists local hosts in URLs; a legitimate compose URL may need a host added.
- The migration guardian flags harmless `DROP NOT NULL` / `DROP DEFAULT` outside contract files.

## Changed files

Libs: libs/common/{settings,time,db,money,supervise,events_dispatch}.py, libs/contracts/events.py. Ops: ops/{__init__,schemas,bootstrap_roles,check_migrations,check_configuration,check_workflows}.py, ops/bootstrap_roles.sql.example. Config: .env.example, docker-compose.yml, Makefile, AGENTS.md, .codex/ (registry and three profiles). Migrations: migrations/20261018_0004_run1_schema_additions.sql, migrations/contract_20261018_0003_retire_socioeconomic.sql. Datagen: datagen/builder.py. Docs: docs/contracts-freeze.md, docs/runs/RUN-r1-a.md, docs/runs/T-007-ci-unit-job.patch. Tests: 17 new files under tests/unit/ (plus snapshots/), and TASKS.md statuses.

## L2 continuation

Not applicable: this run covers only T-003 to T-018. No L2 task was started.
