# Run report r1-e-security

Branch: `agent/r1-e-security`. Python 3.12 venv, `pip install '.[dev,streamlit,identity,sqltest]'` worked.

## Tasks
- T-123 done (commit "T-123 signed service tokens in every client"). Gateway, brief, ingest, seed and verify_live sign a fresh token per request; identity validates signed tokens; the legacy raw secret is removed; core uses per-route allow lists (gateway everywhere; ingest and seed for signal and company upsert; brief for lead and brief-facts reads; seed for company list and opt-out; verify_live for lead list). Core still sets `app.tenant_id` from the body `tenant_id` on company upserts and signals.
- T-080 done (no code changes). Review pack is the final section of `docs/runs/RUN-r1-e.md`, with follow-ups F-r1e-01 to F-r1e-12.
- Blocked: none.

## Commands
- `pytest -q tests/unit/test_internal_auth.py tests/unit/test_service_clients.py`: 36 passed
- `python ops/check_configuration.py`: OK
- `make static`: pass; `make test`: 1151 passed, 2 skipped, 5 xfailed
- `make coverage`: 95.53 percent; `make openapi`, `ops/check_traceability.py`, `make compose-config` (syntax only): pass

## Assumptions
- Compose already gives every sender and validator `INTERNAL_SERVICE_TOKEN` (anchor plus explicit gateway/identity); a new config check enforces it. The seed runs inside the gateway container and signs as `seed`.
- Tokens live 60 s and need synchronized container clocks.

## Changed files
libs/common/internal_auth.py, services/{gateway,brief,ingest,identity,core}, ops/{seed/loader.py,verify_live.py,check_configuration.py}, tests/unit (new service_auth.py, test_service_clients.py; about 20 tests updated to signed tokens, slightly beyond write_scope), TASKS.md, docs/runs/.

## Unresolved risks
- CI e2e (Compose stack, seed through gateway, outbox_dead_letter empty) could not run here (no Docker); a 401 from a missing allow-list entry would only show there.
- One shared HMAC key: the service name is only a claim; only core pins callers.
- Redis ACL users `edge`/`data` and per-service `_app` roles (identity, socio) are not implemented.
- SPEC/docs still describe legacy mode; E2.5 items (analytics schema drop, README, n8n socioeconomic workflow, identity DB settings in compose) fail or are partial. See follow-ups in RUN-r1-e.md.
- No `.github/workflows` change was needed.
