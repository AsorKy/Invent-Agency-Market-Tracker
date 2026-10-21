# RUN r1-f-lens

Branch: `agent/r1-f-lens`. Tasks: T-101, T-133, T-134 (all done).

## Tasks
- T-101 (lead-backend-engineer): `libs/contracts/lens.py` (Lens enum, required quality metrics per lens, pure functions), `ops/check_five_questions.py` (wired into `make static`), `docs/features/_TEMPLATE.md`, `tests/unit/test_lens_contract.py` (6 tests).
- T-133 (second-backend-engineer): `wf_socioeco_refresh.json` and n8n README now use `PULSE_URL` and `/internal/pulse/socioeconomic/refresh`; three new tests in `tests/unit/test_workflows.py` (env vars defined, no ANALYTICS_URL, socio uses PULSE_URL).
- T-134 (coordinator): README (seven services plus Streamlit, seven schemas, pulse), removed `legacy_paths` from the registry, renamed analytics mentions in docstrings of five files. No behavior change.

## Commands
- venv python 3.12, `pip install '.[dev,streamlit,identity,sqltest]'`: OK.
- `make static`: pass. `make test`: 1163 passed, 2 skipped, 5 xfailed.
- `pytest -q tests/unit/test_lens_contract.py`: 6 passed. `pytest -q tests/unit/test_workflows.py`: 34 passed. `python ops/check_five_questions.py`: exit 0.
- test_sql_consistency not run separately: no SQL changed.

## Assumptions
- T-101 uses an enum plus constants, not pydantic models, to avoid changing the contract snapshot. Lens names D, Dx, P, prescriptive, V per REQ-LNS-001. Required metrics for prescriptive (`gate`) and V (`project_metric`, `kill_trigger`) were chosen from ADR 2.9; please confirm.
- Route name `/internal/pulse/socioeconomic/refresh` follows the pulse naming convention.
- Workflow keeps credential `ingest-internal-header`.
- T-133 and T-101 ran in parallel (disjoint scopes), so order differs from the list; no dependencies between them.

## Risks
- Pulse has no socioeconomic refresh route; the workflow (inactive) would fail until one is added.
- The `.env.example`/Settings test accepts variables present only in Settings.
- Workflow credential name still says "ingest" for a pulse call.
- Not tested in CI e2e (no Docker).
