# Run report r1-d2-launch

Branch: `agent/r1-d2-launch`. An earlier session had pushed a report claiming all tasks were blocked on unmerged dependencies; it was based on an older commit. This run merged current `main` (where the dependencies are done) and replaces that report.

## Tasks

| Task | Status | Evidence |
| --- | --- | --- |
| T-074 make invent-tracker | done | 17 tests pass, `make -n invent-tracker` expands |
| T-075 doctor | done | 7 tests pass |
| T-076 verify-live | done | 10 tests pass (fakes only) |
| T-078 integration tier | done | 14 tests collected, skipped without DB/Redis URLs |
| T-079 go-live runbook | done | 5 tests pass |
| T-064 eleven-stage CI | blocked | push of `.github/workflows/ci.yml` rejected (token lacks `workflow` scope) |

## T-064 note

The new workflow is saved as `docs/runs/T-064-ci-workflow.diff` (apply with `git apply`). Its test is saved as `docs/runs/T-064-test_ci_workflow.py.txt`; after applying the diff, move it to `tests/unit/test_ci_workflow.py` (6 tests passed against the new workflow). It is not in `tests/` because it would fail without the workflow change.

## Commands

- venv python3.12 and `pip install '.[dev,streamlit,identity]'`: ok.
- Before each commit: `make static` ok, `make test` ok (final: see PR), `tests/unit/test_sql_consistency.py` 24 passed (no SQL added).
- `pytest -q tests/integration --collect-only`: 14 collected.

## Assumptions and risks

- Integration tests (T-078) never ran against real Postgres/Redis; they use `TEST_DATABASE_URL` and `TEST_REDIS_URL` (the Makefile target does not set them). Use a dedicated Redis DB.
- T-074: no Redis password is generated (compose Redis has no auth). `bootstrap_roles` extensions (vector, postgis, pg_cron) may not exist in `postgres:16-alpine`; owner must confirm with one Docker run. `ops/seed/README.md` still says make targets belong to T-074 (outside scope).
- T-075: live mode requires `BRIGHT_DATA_WEBHOOK_SECRET`, possibly stricter than settings.
- T-076: ingest/core ports are not published by compose; use `VERIFY_LIVE_INGEST_URL`/`VERIFY_LIVE_CORE_URL`. Signal and score hops match by time window; the 5-record bound caps inputs, not records per input; signal source label match unverified live.
- T-064 CI content: deploy steps for backend/frontend/streamlit are placeholders; `integration` job passes the TEST_* variables; e2e/images/contracts jobs untested.

## Changed files

Makefile, TASKS.md, ops/bootstrap_env.py, ops/doctor.py, ops/verify_live.py, tests/unit/test_{make_targets,bootstrap_env,doctor,verify_live,runbook}.py, tests/integration/*, docs/runbooks/go-live.md, docs/runs/T-064-*.
