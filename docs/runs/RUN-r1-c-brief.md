# Run report r1-c-brief

## Tasks
- T-033 done (12 tests passed)
- T-034 done (rules tests passed)
- T-058 done (8 tests passed)
- Blocked: none.

## Commands
- venv python3.12, `pip install '.[dev,streamlit]'`: OK.
- `make static`: OK. `make test`: 571 passed, 2 skipped, 4 xfailed.

## Assumptions
- T-033 and T-034 share the same files in services/brief, so they were committed together (commit "T-033 T-034 ..."), not one commit each.
- `tests/unit/support_brief.py` was added next to the tests (outside the literal write_scope, same support_* convention).
- Rule-based summarizer is primary (D-09); `degraded=true` only when a configured summarizer fails or times out.
- `libs/contracts/tenant_config.py` does not exist; `BriefRules` mirrors the core model locally.
- Ops page uses `pulse/ops/health` (analytics was merged into pulse), departing from ADR 9.2 wording.

## Changed files
services/brief/app/{main,models,rule_based,core_source,service}.py, tests/unit/{support_brief,test_brief_cache,test_brief_singleflight,test_brief_rules,test_streamlit_pages_b}.py, services/streamlit/pages/{5_Pipeline,6_Proyectos,7_Ops}.py, pages/README.md, TASKS.md.

## Unresolved risks
- Core lacks `GET /internal/core/companies/{id}/brief-facts`; brief HTTP source is only tested with fakes.
- BriefRepository is in-memory; Postgres `brief.record_digest` has no `config_version` column.
- Gateway must forward `X-Tenant-Id` to brief (T-032), else default rules are used.
- Core kanban returns `as_of: null`; Ops lacks per-service latency, Bright Data consumption and dbt test endpoints (placeholder shown).
- Kanban card field names are unconfirmed.
