# RUN r1-h-ml

## Tasks
- T-150 done: `PulsePublisher` takes `tenant_id`, sends `X-Tenant-Id` with the signed token, raises before any call when missing; attribution publisher sends the header; kmodes error names the `kmodes` extra.
- T-151 done: migration `20261022_0013_ml_score_features.sql` (six nullable numeric columns on `ml.lead_score`), `upsert_score` stores features and weights, new `GET /internal/ml/explanations/{company_id}` (service token `brief` only, 404 without stored features), added to `NOT_PUBLIC`, table catalog snapshot updated.

## Commands
- venv py3.12 + `pip install '.[dev,streamlit,identity,sqltest]'`: ok
- `make static`: clean. `make test`: 1432 passed, 2 skipped, 4 xfailed.
- `python ops/check_migrations.py`: OK (agent-reported).

## Assumptions
- `ml.lead_score` has no tenant column or RLS, so `set_tenant_context` does not apply to the new read (same as the existing scores route).
- Test fakes `upsert_score` in `test_ml_singleflight.py`, `support_pipeline.py` and tenant args in `test_profiling.py` were adjusted by the coordinator (outside nominal write_scope).

## Risks
- Production callers of `PulsePublisher` (schedule.py hooks) must pass the batch tenant when constructing it; no construction site exists in services today.
- Existing score rows have null features until rescored, so the route answers 404 for them.
