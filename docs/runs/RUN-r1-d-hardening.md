# Run report r1-d-hardening

Branch: `agent/r1-d-hardening`. Tasks: T-060, T-061, T-062, T-070.

## Tasks
| Task | Status | Notes |
| --- | --- | --- |
| T-060 | done (partial scope) | Signed expiring service tokens (`libs/common/internal_auth.py`), tenant header refused without token, gateway never routes `/internal/*`. 12 tests. |
| T-061 | done | 16 fault-injection tests in `tests/unit/test_failure_matrix.py`. |
| T-062 | done | `libs/common/lifecycle.py` (graceful lifespan, 25 s drain, hooks, `/internal/metrics`), JSON logs with `request_id`, wired in all service `main.py`. |
| T-070 | done | Route registry, OpenAPI 3.1 generator, `ops/export_openapi.py`, `docs/api/openapi.json`, `make openapi`. |

## Commands
- Setup: python3.12 venv; `pip install '.[dev,streamlit]'` did not install `cryptography`/`django` (they live in the `identity` extra); installed `.[identity]` additionally.
- Before each commit: `make static` (mypy clean) and `make test` (final: 819 passed, 2 skipped, 4 xfailed). `python ops/check_configuration.py` OK.
- `UPDATE_SNAPSHOTS=1 pytest tests/unit/test_contract_snapshot.py` after the contract change below.

## Contract reconciliation (coordinator decision, T-070)
Pulse response models are the real wire shape, so `libs/contracts/api.py` now follows them: `PipelineBacklogResponse.active/hot/capacity_estimate` nullable; `stale` (required) and `method_version` (nullable) added to the unprocessed-signals, backlog, source-efficiency, time-in-pipeline and top-sources responses; `SourceEfficiency.roas` is `float | None` (a ratio, not money). Snapshot `model_schemas.json` regenerated. Remaining differences: Pulse names the time-in-pipeline model `PipelineTimeResponse`; no contract models exist for market overview, `ops/health`, territory opportunity (documented as free-form objects).

## Unresolved risks / gaps
- T-060: `_app` role per service in settings and Redis ACL users `edge`/`data` in compose checks not done (need `libs/common/settings.py`, `docker-compose.yml`, `ops/check_configuration.py`, outside write_scope). Legacy raw shared secret still accepted by `require_internal_token`; senders (gateway, brief, ingest, identity) must switch to `sign_service_token`. Core requires any valid service token for tenant headers, not gateway-only (`require_gateway_token` exists, not wired because brief/ingest also call core with tenant headers).
- T-062: no worker registers shutdown hooks (relay flush, batch ack) yet; `request_id` not propagated to consumers; metrics are per process; `SHUTDOWN_GRACE_SECONDS` not in `.env.example`.
- T-061 production defects: Brief builds no ml-backed summarizer, so `degraded` is never true in production; Pulse metric endpoints other than market/territory and `ops/health` return 500 instead of 503 on DB errors.
- T-070: core responses without contract models are documented as free-form; gateway registry imports core request models.
- Other sessions' files reformatted by `make static` were reverted, not committed.
