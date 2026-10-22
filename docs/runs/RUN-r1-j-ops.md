# Run r1-j-ops

Branch: agent/r1-j-ops. Tasks: T-164 (done), T-165 (blocked).

## T-164 e2e check with the event chain on
- `ops/e2e_check.py --chain`: after the normal checks, runs a driver inside the gateway container (`python -m ops.e2e_check --drive-chain`) that creates one signal (`seed` service identity) and one lead plus a status change to `qualified` (`gateway` identity) through core's internal API. It then polls until the five outbox chain types (`signal.tagged`, `company.matched`, `signal.explained`, `project.pipeline_updated`, `project.forecast_updated`) are published, none is unpublished and the dead letter count is zero. `signal.scored` goes to the ML stream, not the outbox, so it is not checked.
- `docker-compose.yml`: `EVENT_CHAIN_EMIT: ${EVENT_CHAIN_EMIT:-false}` on core and ml only; `.env.example` unchanged. Default stack and outbox content unchanged.
- CI step as `docs/runs/T-164-ci-workflow.diff` (verified with `patch`); `.github/workflows/` untouched, owner applies it.
- Tests: new cases in `tests/unit/test_e2e_check.py` and `tests/unit/test_compose.py`.
- Step expected to fail first: "Turn the event chain on" (`--wait` for core/ml) is the cheapest to break; after it, the chain check, because the T-159 SQL has only run on fakes.

## T-165 ml feature backfill: BLOCKED
No code written. Rescore inputs cannot be rebuilt from ml alone: the rows to fix have null feature columns, `ml.score_history` holds only score and versions, and features come only from the scoring API request built from core data (INV-01). `ml.lead_score` has no `tenant_id`, so "rows of one tenant" cannot be listed from ml. Options: (a) a core internal endpoint returning features and company ids per tenant, called by the backfill with a service token; (b) accept that old rows get features at their next normal rescore.

## Commands
- venv Python 3.12 and `pip install '.[dev,streamlit,identity,sqltest]'`: OK.
- `pytest -q tests/unit/test_e2e_check.py tests/unit/test_compose.py`: 79 passed. `python ops/check_configuration.py`: OK.
- `make static`, `make test`: see the final lines below.

## Assumptions
- Public routes need a verified JWT for the tenant header and the default stack has identity validation off, so the driver uses the internal API from the gateway container. This deviates from "public routes".
- T-164 was done by the coordinator directly, not delegated.

## Unresolved risks
- Driver and CI step never ran against a real stack (no Docker).
- The driver uses the first company listed and the demo tenant id.
- Final: `make static` OK, `make test` 1567 passed, 2 skipped, 4 xfailed.
