# RUN r1-p-schema

## Tasks
- T-195: done (code and tests written; integration cases not executed locally).

## Changes
- `migrations/20261024_0017_core_signal_tenant_unique.sql`: expand only. A DO block fails with an explicit message if any `(tenant_id, source, source_external_id)` group is duplicated (no deletion), then `CREATE UNIQUE INDEX IF NOT EXISTS signal_tenant_source_external_id_key`. The global unique stays (T-196).
- `services/core/app/repository.py` `create_signal` only: `ON CONFLICT (tenant_id, source, source_external_id)`; the fallback SELECT filters by the same resolved tenant (explicit tenant or the company row tenant).
- `tests/integration/test_sql_r1_flows.py`: three new cases (index in pg_indexes; same-tenant idempotency keeps one row; fallback is tenant filtered with a second tenant holding a different external id). No same-external-id two-tenant case (T-196).
- No ingest change: ingest has no upsert on core.signal.

## Commands
- venv python3.12 + pip install extras: OK
- `python ops/check_migrations.py`: OK
- `pytest -q tests/unit/test_sql_consistency.py tests/unit/test_openapi.py`: 35 passed
- `make static`: OK
- `make test`: 1735 passed, 2 skipped, 4 xfailed
- Integration cases: skipped locally (no Docker/Postgres); CI integration stage is the check.

## Assumptions
- `tenant_id` null on SignalCreate is resolved from core.company exactly as the insert does.

## Risks
- Until T-196, a second tenant with the same external id still fails on the global unique (accepted, D-37 option B).
- Integration cases unverified on real Postgres until CI runs.
