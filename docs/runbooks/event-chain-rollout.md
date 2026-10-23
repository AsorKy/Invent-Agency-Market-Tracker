# Event chain rollout: turning on EVENT_CHAIN_EMIT

This runbook is the operator procedure for setting `EVENT_CHAIN_EMIT=true`. It satisfies REQ-DOC-001 and REQ-EVT-001. It lists variable names only; never write a secret into this file. The flag is declared in `libs/common/settings.py` (`event_chain_emit`, default false) and in `.env.example` as `EVENT_CHAIN_EMIT=false`; `ops/check_configuration.py` fails when the template default is not false. Every service reads `.env` through `env_file` in `docker-compose.yml`, so the flag is changed in `.env` and the producer services are recreated.

## 1. What the flag changes

With the flag off, no event differs from before: `chain_events_after` in `libs/domain/event_chain.py` returns an empty list when `enabled` is false. With the flag on, producers add chain events next to the existing ones:

| Producer | Event types added | Source |
| --- | --- | --- |
| `create_signal` | `signal.tagged` (and a stamped `signal.created`), same transaction | `services/core/app/repository.py` |
| `create_lead` | `company.matched`, `signal.explained` | `services/core/app/leads.py` |
| `change_status` | `project.pipeline_updated`, `project.forecast_updated` | `services/core/app/leads.py` |
| `ScoringService.regenerate` | `signal.scored`, written to the Redis stream | `services/ml/app/scoring_service.py` |

`funnel.gate_decision` is emitted by the funnel engine whatever the flag says.

## 2. Order: consumers first, then producers

1. Consumers. The relay (`services/core/app/outbox_relay.py`), the Pulse consumer (`services/pulse/app/consumer.py`) and the ML consumer (`services/ml/app/consumer.py`) already acknowledge chain event types without a dead letter; `tests/unit/test_event_chain_consumers.py` proves it for each of the three. Confirm that every running image contains this code before the next step. The brief service has no event consumer.
2. Producers. Set `EVENT_CHAIN_EMIT=true` in `.env`, then recreate core and ml (`docker compose up -d core ml`, adding the service names your stack uses). Do not flip it for one producer only: a partial chain is harder to read.

## 3. Checks before the change

Run them with `docker compose exec postgres psql` as in `ops/e2e_check.py`, and keep the output for the comparison.

1. Outbox backlog: `SELECT count(*) FROM core.outbox_event WHERE published_at IS NULL` must be 0, or at least stable.
2. Dead letters: `SELECT count(*) FROM core.outbox_dead_letter` must be 0.
3. `python ops/e2e_check.py` exits 0 with the flag off.

## 4. Checks after the change

1. Wait for the relay to drain, then repeat the backlog query: it must return to 0.
2. The dead letter count must still be 0. Any row there means a consumer rejected a chain type: set the flag back to false (section 5) and read the `error` column.
3. The `--chain` mode of `ops/e2e_check.py` (added by T-164; it does not exist until that task is merged) creates one signal, moves one lead status, and requires the chain event types in the outbox to be published. Until then check by hand: `SELECT event_type, count(*) FROM core.outbox_event GROUP BY event_type` must list `signal.tagged`, `company.matched`, `signal.explained`, `project.pipeline_updated` and `project.forecast_updated` after you create one signal and move one lead.
4. Audit one decision with `trace_chain` (`libs/domain/event_chain.py`): pass it the outbox events of one company as mappings with `event_id`, `event_type` and `payload`. Read `path`, `complete`, `gap` and `problems`.

## 5. Rollback

1. Set `EVENT_CHAIN_EMIT=false` in `.env` and recreate core and ml. New events then match the pre-flag content, because `chain_events_after` returns an empty list.
2. Chain events already written stay valid. The relay publishes them and the consumers record and ignore the types they do not use (`tests/unit/test_event_chain_consumers.py`), so old consumers need no change and no replay.
3. Do not delete the rows. If a dead letter appeared, use `services/core/app/dead_letter.py` and the replay command in `services/core/app/republish.py` only after the cause is fixed.

## 6. Known limit: trace_chain gaps until the parent id is propagated

Decision D-21 (owner, 2026-10-22) no longer accepts the gaps as a design. The contract carries `parent_event_id` (T-174, T-175) and `signal.scored` leaves `ml` through its outbox (T-176). T-188 closes the production callers only in part; `trace_chain` still reports a gap at the joins listed as open.

Closed by T-188 (callers in code, each id optional so a missing id never fails a request):
- `funnel.py`: `evaluate_project_gate` takes an optional `on_gate_event` hook and hands the written `funnel.gate_decision` event (`event_id`, `event_type`, `source_version`) to the caller, who passes its id as `LeadStatusPatch.gate_event_id`. The route response is unchanged. No route links a gate decision to a lead stage change yet, so the hook is not wired to a route.
- `services/ml/app/backfill.py`: forwards `parent_event_id` to `ScoreFeatures` when the features payload from core carries one, and leaves it empty otherwise.

Still open (follow-up task needed, outside the T-188 scope):
- core to ml `signal.tagged` id: the internal features route (`services/core/app/score_features.py`, `services/ml/app/core_features.py`) does not return the id of the company's `signal.tagged` event, so the backfill always sends an empty parent and `signal.tagged` to `signal.scored` still uses `reference_parent`.
- core storage of the `signal.scored` id: core cannot read `ml.outbox_event` (INV-01) and stores no score event id, so lead creation (`main.py`, `imports.py`) cannot fill `LeadCreate.scored_event_id` and `signal.scored` to `company.matched` still uses `reference_parent`. It needs an additive column, a core worker handler for `signal.scored` and a read in the lead create call.
- `funnel.gate_decision` to `project.pipeline_updated` stays on `reference_parent` until a route passes the hook's id to the stage change.

Until those are done, a gap at the open joins is the known limit, not a failed rollout. See `docs/runs/RUN-r1-i-events.md` for the origin of the limit.
