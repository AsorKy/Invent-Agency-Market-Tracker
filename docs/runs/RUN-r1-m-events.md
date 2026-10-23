# Run r1-m-events

Branch: agent/r1-m-events. Tasks T-174, T-175, T-176, T-177: all done. One commit each, coordinator committed, subagents did the work.

## Tasks
- T-174: typed `ChainPayload` models for the six chain events in `libs/contracts/events.py` (`extra="forbid"`, `schema_version`, mandatory nullable `parent_event_id`), registry `CHAIN_PAYLOAD_MODELS`, helper `validate_chain_payload` (derives `parent_event_id` from the `parent` block when the key is absent). Transitional optional fields: `signal.scored` (`signal_id`, `score_version`), `company.matched.rues_matched`, `signal.explained` (accepts `reason`).
- T-175: `stamp_payload` writes top-level `parent_event_id`; additive optional `ScoreFeatures.parent_event_id`, `LeadCreate.scored_event_id`, `LeadStatusPatch.gate_event_id`; offline test closes the full chain (`complete` true, `gap` None). Minimal echo of the parent id in `services/ml/app/scoring_service.py`. `docs/api/openapi.json` and snapshots regenerated, additive.
- T-176: shared relay `libs/messaging/relay.py` (core `dead_letter.py` is a thin wrapper), migration `20261023_0016_ml_outbox.sql` (`ml.outbox_event`, `ml.outbox_dead_letter`, no RLS, in `RLS_EXEMPT`), ml relay instance and compose service `ml-relay`, `signal.scored` written to the ml outbox in the score transaction when `EVENT_CHAIN_EMIT` is on.
- T-177: Pulse dispatches `project.pipeline_updated` and `project.forecast_updated`: updates `pulse.project_snapshot.stage`, closes the open `pulse.project_stage_visit` row (`exited_at` is in that table, not project_snapshot) and opens a new visit; idempotent by inbox and by `IS DISTINCT FROM`.

## Commands (Python 3.12 venv, pip install worked)
- Per task: acceptance pytest sets, `make static`, `make test`. Final: `make static` OK, `make test` 1649 passed, 2 skipped, 4 xfailed. `ops/check_migrations.py` and `ops/check_configuration.py` run inside make static.
- `tests/unit/test_event_chain_contracts.py` and `tests/unit/test_outbox_relay.py` named in acceptance do not exist; `test_outbox.py` and `test_relay_wiring.py` were run instead.

## Assumptions
- T-177 opens a new stage visit row so the next change has one to close. Null `project_id` or a missing snapshot changes nothing (`service_line` NOT NULL).
- The migration does not `ALTER ... OWNER TO ml_app`; it follows the existing pattern (migrating role owns, `bootstrap_roles` grants).
- With the flag on and no tenant, no `signal.scored` outbox row is written (the relay would dead-letter it).

## Unresolved risks
- Real Postgres never ran: migration, ml outbox SQL and the three new integration cases are unverified (no Docker). The CI e2e stage runs the new `ml-relay` service; it must be watched for dead letters. Flag stays off by default.
- Production callers do not yet send the new parent ids: `services/core/app/funnel.py` does not expose the gate decision event id nor pass `parent_event` (outside scope), `services/ml/app/backfill.py` builds `ScoreFeatures` without `parent_event_id`, and core `main.py`/`imports.py` call `create_lead` without `scored_event_id`. Until then production falls back to `reference_parent` and `trace_chain` still shows those gaps. Follow-up task needed.
- `signal.scored` now arrives after the relay poll, so it no longer follows `lead.score_updated` in the stream order; the chain links by id.
- Parent `source_version` is empty for scored/gate parents in `leads.py`.
- Adding `ml-relay` changes the Compose stack (one more worker, no ports).
