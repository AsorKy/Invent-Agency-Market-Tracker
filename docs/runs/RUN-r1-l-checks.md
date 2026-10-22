# Run r1-l-checks

Tasks done: T-171 (qa-reviewer), T-173 (second-backend-engineer). Both depends were done (T-168, T-165, T-161).

## Commands
- python3.12 venv, `pip install '.[dev,streamlit,identity,sqltest]'`: ok
- `pytest -q tests/unit/test_e2e_check.py tests/unit/test_demo_pipeline.py`: pass
- `pytest -q tests/unit/test_doctor.py tests/unit/test_runbook.py && python ops/check_language.py`: pass
- `pytest -q tests/unit/test_sql_consistency.py`: pass (it scans only services/ and libs/, not the new ops/ SQL)
- `make static`: pass; `make test`: 1618 passed, 2 skipped, 4 xfailed

## Assumptions
- Tenant threshold is read from `core.tenant_config.brief_rules.alert_rules.min_score_for_qualified`, falling back to the AlertRules model default.
- If no company reaches the threshold the lead check passes with 0 of 0.
- `tests/unit/test_runbook.py` got one extra needle (outside the listed write_scope, allowed by the T-173 note).

## Risks
- No Docker: the CI e2e stage is the real check. Order of assertions: existing data checks, then qualified companies have an auto_qualification lead (most likely first failure), then the ml backfill and zero rows without features, then `--chain`.
- The new SQL in `ops/` (e2e_check, doctor) is not covered by test_sql_consistency; column names were checked by hand against migrations.
- No workflow files changed, no migrations added.
