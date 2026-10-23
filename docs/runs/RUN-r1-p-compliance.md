# Run r1-p-compliance

## Tasks
- T-191 done: opt-out and purge are tenant-aware (D-36, option A).

## What changed
- `POST /internal/core/compliance/opt-out` requires `X-Tenant-Id` (400 `tenant_required`) and the unit of work sets the tenant context before the first statement; a company of another tenant is `already_removed`, never deleted.
- Purge route and `python -m services.core.app.retention` get tenant ids from `GET /internal/identity/tenants` (new `identity_tenants.py`, signed token of `core`) and run `purge_tenants`: one transaction and context per tenant, a failing tenant is listed under `failed` and does not stop the others. CLI exit code 1 when any tenant failed, 2 when identity is unreachable. The route answers 503 when identity is unreachable.
- `PurgeBlindError`: a purge or opt-out that deletes zero company rows for a company that exists now fails and rolls back.
- Audit entries write the `tenant_id` column; payloads still hold only the hash, channel, counts.
- No migration, no new role, no policy, no public route, no OpenAPI change (core routes are not in the public spec; `make openapi` and snapshots regenerated with no diff).

## Commands
- venv py3.12 and pip install of the extras: OK.
- `make static`: green. `make test`: 1739 passed, 2 skipped, 4 xfailed. `test_sql_consistency` and `test_openapi`: pass.
- `pytest -q tests/unit/test_retention.py`: 20 passed.
- Integration cases (`test_opt_out_of_tenant_a_deletes_only_its_rows_as_core_app`, `test_retention_purge_runs_each_tenant_separately_as_core_app`) NOT run: no Docker/Postgres in the sandbox; CI integration stage is the real check. They assert that no context sees nothing.

## Assumptions
- Tenants are listed as active only (identity route). Purge audit `tenant_id` comes from the context column, not the payload.

## Unresolved risks
1. `ops/seed/loader.py` `HttpSeedCore.opt_out` (seed `clear`) sends no `X-Tenant-Id` and will now get 400. It must send `DEMO_TENANT_ID`; outside this task's write_scope. The e2e does not call clear.
2. `docker-compose.yml` gives core no `IDENTITY_URL` (default `http://localhost:8005`), so the purge route/CLI cannot reach identity in the Compose stack. Needs `IDENTITY_URL: http://identity:8000` for core (devops scope); noted in go-live item 7.
3. `GET /internal/core/companies` used by the seed has no tenant context (pre-existing, under RLS).
4. No public gateway route for opt-out; T-197 / owner decision.
