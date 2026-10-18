# Run report r1-b-c (lane C, part 1)

Branch: `agent/r1-b-c`. Python 3.12 venv at `/tmp/venv`; `pip install '.[dev]'` succeeded.

## Tasks
Done: T-050, T-051, T-052, T-053, T-054, T-055, T-056, T-057, T-059 (one commit each).
Left `todo` as instructed: T-058 (depends on lane A, T-022).
Blocked: none.

## Commands
`make static` and `make test` run before each commit and at the end: static clean; final `make test` 348 passed, 2 skipped, 4 xfailed.
Per-task acceptance pytest commands passed (see `evidence:` lines in TASKS.md). T-055 used `dbt parse` with a copy of `profiles.yml.example` in `/tmp/dbtprof` (exit 0).

## Assumptions
- T-050: coordinator removed analytics tests from `tests/unit/test_outbox.py` and `analytics` from `tests/unit/test_health.py` (outside write_scope, needed after deleting the service).
- T-051: `stale` means older than 26 h (ADR wording is inverted; REQ-PUL-001 followed). `ops/health` content is a design choice.
- T-052: payload fields `city`, `service_line`, `hot` and the `lead.outcome_recorded` shape are assumed.
- T-054: pure-python K-Prototypes fallback; UMAP+HDBSCAN path untested (ml extra not installed).
- T-055: schema `pulse` per SPEC (ADR says `analytics`); hot-lead threshold 70 guessed; dim_sector and dim_service_line derived.
- T-056: stale threshold 15 min; pyproject got a `streamlit` extra plus mypy overrides so `make static` passes without streamlit installed.
- T-059: entropy weights instead of PCA (numpy not a base dependency); `prospect_density` is a CSV column.

## Changed files
services/pulse (consumer, rollups, rollup_close, socio_loader, models, repository, main, procs.toml), services/analytics (deleted), services/ml (scoring_service, segmentation, main, repository), services/streamlit (Home.py, app/helpers.py, app/ui.py, pages/1-4, _common), transform/ (staging, marts, semantic.yml, export_parquet.py), libs/contracts/pulse.py, libs/domain/scoring.py, libs/domain/socio.py, tests (new unit tests, tests/fixtures/socio), pyproject.toml, TASKS.md.

## Unresolved risks
- T-055: dbt marts named like migration-created `pulse.fact_*` tables would replace them on first `dbt run`; needs a decision (alias/schema or dbt ownership). Literal acceptance with `--profiles-dir transform` fails until a profiles.yml exists. `export_parquet.py` needs `duckdb` (not a dependency).
- The outbox relay does not publish `tenant_id` in stream messages; the Pulse consumer skips events lacking it, so nothing is projected until core/relay adds it.
- Gateway and `libs/common/settings.py` still reference analytics (T-032/T-073); gateway routes for pulse market/segments, `ops/health` and `territory/opportunity` are not wired.
- `libs/contracts/api.py` (frozen) disagrees with Pulse models on nullability (`PipelineBacklogResponse`, `roas`).
- T-059 does not persist to `socio.indicator_*` (needs migration); computed on read.
- T-053: `refresh_staleness` unwired; no regeneration queue. Tenant weights arrive via `X-Tenant-Id` header.
- Streamlit: nothing sets `session_state["role"]`; `STREAMLIT_DEV_ROLE` dev default must not be set in production. Gateway paths used by pages are guesses; `pages/README.md` is outdated. Rollup/segmentation SQL tested only via fakes.
