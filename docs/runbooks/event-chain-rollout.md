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

## 6. Known limit: trace_chain gaps by design

The steps run in different services and transactions, so only three segments are linked by real event ids: `signal.created` to `signal.tagged`, `company.matched` to `signal.explained`, and `project.pipeline_updated` to `project.forecast_updated`. The three joins (`signal.tagged` to `signal.scored`, `signal.scored` to `company.matched`, `funnel.gate_decision` to `project.pipeline_updated`) use deterministic reference ids made by `reference_parent` in `libs/domain/event_chain.py`. No event carries those ids, so `trace_chain` reports a gap at each join and `complete` stays false. This is expected until the event contract carries the parent event id (see `docs/runs/RUN-r1-i-events.md`). Do not treat these gaps as a failed rollout.

Also note that `signal.scored` goes straight to the Redis stream, not through the outbox, so it is absent from the outbox queries above.
