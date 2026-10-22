# RUN r1-k-features

Branch: agent/r1-k-features. Tasks: T-165 done, T-169 done (T-168 belongs to another session).

## Commands
- venv python3.12, `pip install '.[dev,streamlit,identity,sqltest]'`: OK.
- T-165: acceptance pytest list 91 passed; `make static` OK; `make test` 1602 passed. Real PostgreSQL 16 (throwaway cluster): tests/integration/test_sql_score_features.py 3 passed as core_app.
- T-169: `pytest -q tests/unit/test_runbook.py` 10 passed; `ops/check_language.py` OK; make static OK; make test 1603 passed.

## Assumptions / deviations
- Route GET /internal/core/admin/score-features; another service's token answers 401 (same as score-rules), not 403. limit>500 is clamped; limit=0 gives 422.
- Features are the seed demo heuristic, a placeholder until the real feature definition is decided. Seed output is pinned by a test.
- Backfill: `python -m services.ml.app.backfill --tenant <id> --limit N`; stops with exit 1 if rules or features are unavailable. Prints counts only.
- openapi.json unchanged (internal route). No migration.

## Risks
- First CI failure expected: tests/integration/test_sql_score_features.py (real RLS, signal.tenant_id filter, COALESCE(city,'') grouping); it passed locally.
- ruff E501 at tests/unit/test_runbook.py line ~133 is pre-existing, not covered by make static.
