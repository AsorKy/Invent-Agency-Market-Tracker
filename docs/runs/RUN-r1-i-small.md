# RUN r1-i-small

Tasks done: T-158, T-160, T-161 (all dependencies were done).

## Commands
- venv python3.12, `pip install '.[dev,streamlit,identity,sqltest]'`: ok
- per-task acceptance pytest: T-158 59 passed + `ops/check_configuration.py` OK; T-160 14 passed; T-161 19 passed
- `make static`: green; `make test`: 1505 passed, 2 skipped, 4 xfailed

## Assumptions
- T-158: `app_env` already carried INTERNAL_SERVICE_TOKEN; only CORE_URL was added.
- T-160: `main()` only runs the nightly drift loop; `run_weekly`/`run_monthly` are not called yet, `build_publisher` is the construction site.
- T-161: new queries use the existing superuser psql path (no INV-01 change). The CI e2e stage will first hit "core.company rows have no score" if the core worker did not apply `lead.score_updated`.

## Risks
- Demo test works around the shared `support_pipeline` fakes (which drop features and `store_score`) via monkeypatch; wiring into the fakes belongs to their owner.
- CI e2e not runnable here (no Docker).
