# Run report r1-c-core

Branch: `agent/r1-c-core`. Setup: Python 3.12 venv, `pip install '.[dev,streamlit]'` succeeded.

## Tasks
| Task | Status | Evidence |
| --- | --- | --- |
| T-090 | done | 24 targeted tests passed; `tenant_id` is now its own stream field |
| T-091 | done | 12 targeted tests passed; `check_configuration` OK |
| T-092 | done | 5 end-to-end offline tests passed |
| T-035 | done | 7 tests passed |
| T-049 | done | 31 tests passed; `check_workflows` OK (9) |

None blocked. Before each commit `make static` and `make test` passed (final: 578 passed, 2 skipped, 4 xfailed).

## Changes
- T-090: `tenant_id` added to every `core.outbox_event` INSERT and to the add_outbox_event protocols and callers; `stream_fields` requires a tenant; the relay dead-letters null-tenant rows (`missing_tenant_id`, ALERT); republish skips them; source-scan test. Fixtures in `test_dead_letter.py` and `test_republish.py` gained `tenant_id`.
- T-091: `outbox_relay.main` delegates to `dead_letter.main`; legacy loop and SQL removed; wiring test.
- T-092: `tests/unit/test_relay_to_consumers.py`, `tests/unit/support_relay.py`.
- T-035: `services/core/app/hot_alert.py`, hooks in `qualification.py`, `leads.py`, `main.py`, `worker.py`.
- T-049: six new n8n workflows, hot lead alert body fix, README, tests.

## Assumptions
- Signal outbox tenant comes from the company row (latest version).
- T-035: no Hot event exists, so a post-commit `on_hot` callback is used. Tenant is read from the DB row, not the payload and not the stream field (deviation from the task note).
- n8n placeholders: Bright Data dataset names, `/internal/analytics/socioeconomic/refresh`.

## Unresolved risks
- Hot alert dedupe and retries are in process memory and lost on restart; a durable fix needs an outbox event or migration.
- The ml consumer ignores the stream `tenant_id` (it uses no tenant); brief has no consumer. Pulse reads the field first.
- Pulse events from core lack `city`, `service_line` (signals) and `hot`, so rollups land under `unknown`.
- `RUES_DELTA_URL`, `OPS_ALERT_FROM`, `OPS_ALERT_TO` are used by new workflows but missing from `.env.example` (coordinator-owned; not edited).
- Signals for a company with no row produce a null-tenant outbox event that is dead-lettered by design.
- No push touched `.github/workflows`.
