# Run r1-l-platform

Tasks done: T-170, T-172 (and T-000 closed by T-172). Blocked: none.

## Commands
- `python3.12 -m venv /tmp/venv && pip install '.[dev,streamlit,identity,sqltest]'`: OK.
- `pytest -q tests/unit/test_compose.py`: 56 passed. `python ops/check_configuration.py`: OK (106 variables).
- `make static`: OK. `make test`: 1607 passed, 2 skipped, 4 xfailed.
- `ruff check tests` (outside make static): 15 findings before, 14 after.

## Changes
- T-170: postgres and redis ports are `${POSTGRES_BIND:-127.0.0.1}:5432:5432` and `${REDIS_BIND:-127.0.0.1}:6379:6379`; new test requires an explicit bind on both.
- T-172: RUN-smoke.md, T-000 done, E501 in test_runbook.py fixed.

## Assumptions
- The task asked for commented overrides in `.env.example`, but check_configuration.py only counts uncommented lines and fails on Compose variables missing there, so the two variables are uncommented with the default value (no behaviour change).

## Unresolved risks
- No Docker here: the CI e2e stage is the real check of the stack. The default bind is loopback, so host tools keep working.
- Remaining `ruff check tests` findings, not fixed (files not created by this wave): test_attribution.py:24 B905; test_connectors.py:44, test_connectors_more.py:50,73, test_funnel.py:158, test_optional_extras.py:54, test_profiling.py:77, test_unique_indexes.py:93, test_workflows.py:3,25 E501; test_event_chain_producers.py:9 F401 and :168 B023; test_score_event.py:20 and test_workflows.py:8 I001.
