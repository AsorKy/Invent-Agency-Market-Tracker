# RUN r1-i-validation

Tasks done: T-146, T-147, T-148 (in order, one commit each).

## Commands
- venv python3.12, pip install '.[dev,streamlit,identity,sqltest]': OK.
- Before each commit: make static OK; make test 1510 / 1531 / 1543 passed (2 skipped, 4 xfailed).
- make openapi twice: no further diff.

## Assumptions
- Project version lives in core.project.metadata->>'version' (default 1); only KILL bumps it.
- V1, V2, V4, V5 read optional core.project.metadata->'validation'; nothing writes it yet. V3 and V6 come from core.lead/core.company.
- V6 tenant-wide row uses project_id 00000000-0000-0000-0000-000000000000.
- T-148 needed edits outside its listed scope, approved by coordinator: gateway main.py `_authorize` (allowed_roles), routes.py, test_gateway_routes.py.

## Unresolved risks
- SQL not run against Postgres (jsonb_set, FOR UPDATE, RLS on audit_log); only offline consistency tests.
- Empty cards: psi (needs ml, INV-01), model_utility, decision_latency_days.
- Non-KILL decisions take no version; concurrent ADVANCE and KILL both succeed.
- Funnel metrics are per service_line, not truly per project.
- Roles bd_rep/intake now get 403 instead of 404 on these paths.
- oasdiff not run; diff is additive only.
