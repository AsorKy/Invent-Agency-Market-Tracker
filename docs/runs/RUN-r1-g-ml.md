# RUN r1-g-ml

Branch: agent/r1-g-ml. Tasks: T-139, T-141, T-137, T-138 (all done; see notes).

## Tasks
- T-139: extras `kmodes`, `graph`, `topics` with upper bounds; `tests/unit/test_optional_extras.py` (AST scan, lazy-import subprocess check).
- T-141: root cause of empty `ml.lead_score` / `brief.record_digest`: nothing ever called scoring or brief generation (ML consumer only marks scores stale). Added `ops/seed/enrich.py` (runs after `load`, signed service tokens, rule-based brief digest, no LLM key). Required in `ops/e2e_check.py`: core.company, core.signal, pulse.fact_signal, pulse.daily_rollup, ml.lead_score, brief.record_digest. Informational: core.brief (keyed by project; the demo creates none), ingest.raw_snapshot.
- T-137: brief optional `drivers`/`counterfactual` via `services/brief/app/explanations.py` (port + HTTP client, failure-tolerant); contract snapshot and OpenAPI additive.
- T-138: `PulseSeriesLoader`, `run_nightly` (max_runs stop condition), authenticated webhook post (header from env), new `wf_drift_alert.json`, n8n credential renamed to pulse. `tests/unit/test_drift.py` (outside scope) updated by coordinator for the async loader and header.

## Commands
- venv python3.12, `pip install '.[dev,streamlit,identity,sqltest]'`: ok.
- Final `make static`: clean. `make test`: 1320 passed, 2 skipped, 4 xfailed.
- `make openapi`, `UPDATE_SNAPSHOTS=1` snapshot: additive only. `python ops/check_configuration.py`: OK.

## Assumptions
- Brief calls `GET /internal/ml/explanations/{company_id}` on ml.
- Drift series come from pulse weekly unprocessed-signals (no dedicated endpoint).

## Unresolved risks
- ml has no explanation route, so brief drivers stay null until one is added (follow-up).
- No Compose drift-worker entry: `tests/unit/test_compose.py` EXPECTED is out of scope. Suggested entry: command `python -m services.ml.app.schedule`, `<<: *app_env`, depends on pulse healthy.
- New optional env vars undocumented in `.env.example`: DRIFT_ALERT_WEBHOOK_URL, DRIFT_ALERT_HEADER_NAME/VALUE, DRIFT_TENANT_ID, DRIFT_RUN_HOUR_UTC.
- n8n credentials `pulse-internal-header` and `drift-alert-header` must be created by the operator.
- Offline only: the CI e2e stage is the real check for T-141 (Postgres RLS paths, core worker lead creation).
- profiling.py error text for kmodes still says the `ml` extra.
