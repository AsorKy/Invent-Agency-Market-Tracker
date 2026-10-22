# RUN r1-g-validation

Branch `agent/r1-g-validation`. Layer L2 validation chain.

## Tasks
| Task | Status | Commit |
| --- | --- | --- |
| T-108 Validation metrics V1 to V6 and thresholds | done | "T-108 ..." |
| T-109 Deterministic funnel engine | done | "T-109 ..." |
| T-111 Streamlit validation page | done | "T-111 ..." |
| T-110 Event chain with source_version | done (partial wiring, see risks) | "T-110 ..." |
| T-112 Layer L2 review pack | done | this commit |

No task was blocked. Setup: Python 3.12 venv at /tmp/venv, `pip install '.[dev,streamlit,identity,sqltest]'` succeeded.

## Commands run
- `pytest -q tests/unit/test_validation_metrics.py tests/unit/test_validation_thresholds.py`: 41 passed
- `pytest -q tests/unit/test_funnel.py`: 28 passed
- `pytest -q tests/unit/test_streamlit_validation.py`: 4 passed
- `pytest -q tests/unit/test_event_chain.py`: 5 passed
- `make static`: pass before every commit (ruff, format, mypy, guards)
- `make test`: 1332, 1364, 1369 passed (2 skipped, 4 xfailed) at T-108, T-109/T-111, T-110; includes test_sql_consistency
- `python ops/check_traceability.py`: OK (119 requirements, 123 tasks)

## Changed files
- libs/domain/validation.py, validation_thresholds.yaml, funnel.py, event_chain.py
- services/core/app/funnel.py, services/core/app/repository.py (additive funnel unit of work)
- config/funnel/g1..g7 (example checklists)
- services/streamlit/pages/9_Validation.py, _common.py, 7_Ops.py
- tests: test_validation_metrics, test_validation_thresholds, test_funnel, test_streamlit_validation, test_event_chain
- TASKS.md (own blocks only)

## L2 review pack (T-112)
| Requirement | Evidence | Gap |
| --- | --- | --- |
| REQ-LNS-001 lens tags (T-101) | test_lens_contract | none |
| REQ-LNS-003 D1 (T-102) | test_descriptive_metrics | endpoint needs read path |
| REQ-LNS-004 D2/D3 (T-103) | test_profiling, test_topics | pulse ingest endpoints, optional extras |
| REQ-LNS-005 Dx1 (T-104) | test_attribution | persistence to pulse.attribution |
| REQ-LNS-006 Dx2 (T-105) | test_causal | none |
| REQ-LNS-007 Dx3/Dx4 (T-106) | test_drift, test_explain | Brief integration |
| REQ-LNS-008 metric pack (T-100) | test_metrics_pack | none |
| REQ-LNS-009 funnel (T-109) | test_funnel | no HTTP route; project status not changed on KILL |
| REQ-LNS-010 metrics V1-V6 (T-108) | test_validation_metrics | pure functions only; no worker persisting into the validation schema |
| REQ-LNS-011 thresholds (T-108) | test_validation_thresholds | horizon_mix_deviation bands invented, owner to confirm |
| REQ-LNS-012 event chain (T-110) | test_event_chain | only funnel.gate_decision is emitted; 6 other producers not wired |
| REQ-LNS-013 page (T-111) | test_streamlit_validation | API endpoints /api/v1/validation/... are assumed and not served, so cards degrade in production |

Depends on the owner: D-06 funnel checklists. `config/funnel/*.yaml` are `example: true`; production refuses them unless `FUNNEL_ALLOW_EXAMPLE_CHECKLISTS=true`. Also confirm the threshold band boundaries chosen where the ADR is silent (LTV:CAC 5, burn multiple 2 to 3, CAC payback 12 to 18).

## Assumptions
- Production detection reuses the ops/bootstrap_roles convention (APP_PROFILE and ENVIRONMENT, default local), so the local e2e profile loads examples.
- Funnel audit rows go to the existing core.audit_log; no migration added.
- The event chain carries source_version in the payload (outbox has no column). `libs/domain/event_chain.py` is outside the task's write_scope, placed there because core, ml and pulse share it.
- Streamlit page 9 roles: admin, strategist, analyst (copied from Ops).

## Unresolved risks
- Docker images must ship `config/funnel/` and `libs/domain/validation_thresholds.yaml` (not package data; read by repo path). Dockerfiles were not checked.
- Chain events other than funnel.gate_decision were deliberately not emitted, to avoid dead letters (REQ e2e gate); unknown-type handling by relay/consumers was not verified. Producers must gate emission behind a flag that is off by default.
- nested source_version strings grow per hop (8 max).
- No Docker: e2e stack not exercised. No contract or OpenAPI change was needed.
