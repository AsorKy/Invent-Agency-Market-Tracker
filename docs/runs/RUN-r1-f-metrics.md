# RUN r1-f-metrics

Branch: agent/r1-f-metrics. Layer L2 analytics chain. All six tasks done, none blocked. One commit per task.

## Tasks done
| Task | Summary | Result |
| --- | --- | --- |
| T-100 | `libs/domain/metrics/`: AUC, PR-AUC, Brier, reliability, lift@K, precision@K by group, NDCG@K, MAP, MRR, sMAPE, MASE, pinball, coverage, Qini | 39 tests |
| T-102 | `services/pulse/app/descriptive.py`: percentiles, MAD, IQR, HHI, Gini, project metrics; additive optional fields on three Pulse responses | 10 tests |
| T-104 | `libs/domain/attribution.py` (last/first-touch, time-decay, Markov, gated Shapley) and `services/ml/app/attribution_service.py` | 8 tests |
| T-105 | `libs/domain/causal.py`: DID, synthetic control, propensity matching, E-value, inconclusive on sign flip (stdlib only) | 8 tests |
| T-103 | `libs/domain/topics.py`, `services/ml/app/{profiling,communities,publisher,schedule}.py` | 12 tests |
| T-106 | `libs/domain/drift.py`, `services/ml/app/{drift_worker,explain}.py` | 15 tests |

## Commands
Setup: python3.12 venv at /tmp/venv, `pip install '.[dev,streamlit,identity,sqltest]'` succeeded.
Before each commit: `make static` (clean) and `make test` (run by the subagent, then pytest of the new file by the coordinator; final make test: 1246 passed, 2 skipped, 5 xfailed). `make openapi` ran after T-102 (unchanged). `tests/unit/test_sql_consistency.py` is part of `make test`; no SQL was added.
Push was done with `git push` over the repository remote (it worked), not the GitHub file tools.

## Assumptions
- Definitions: unscaled MAD, HHI on 0..1, linear-interpolation percentiles, unnormalized Qini, sMAPE in percent.
- Optional heavy libraries (kmodes, igraph, leidenalg, yake, gensim, shap) are lazy and not installed; their paths are untested against real libraries.
- Estimators implemented in pure stdlib to stay deterministic and dependency free.

## Unresolved risks and follow-ups
- T-102: no project-metrics endpoint; needs a read path (SQL) in a later task.
- T-104: persistence to `pulse.attribution` not implemented (no migration in scope).
- T-103: Pulse ingest endpoints `POST /internal/pulse/ingest/{profiles,communities,topics}` do not exist; contract is in `publisher.PATHS`. `pyproject.toml` extras (`kmodes`, `graph`, `topics`) are not added (outside write_scope).
- T-106: Brief does not call `build_explanation` yet and has no field for drivers/counterfactuals; the drift worker has no real loader or schedule; n8n post has no auth header.
- SPEC.md `tasks:` lines for REQ-LNS-003..008 still say "none yet" (not edited by design).
- Causal SE/CI for PSM and synthetic control are approximate.
