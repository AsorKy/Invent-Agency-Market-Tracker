# Run r1-i-events

Branch: agent/r1-i-events. Task: T-159 (done). Done by the coordinator directly (one task, no parallel scope).

## T-159 Event chain producers behind EVENT_CHAIN_EMIT
All producers call `chain_events_after(..., enabled=get_settings().event_chain_emit)`, in the same transaction as the parent where one exists.
- `create_signal` (core repository): stamps `signal.created` with `source_version` and writes `signal.tagged` (real parent) in the same transaction, tenant from the company row under the tenant context.
- `create_lead` (core leads): `company.matched` then `signal.explained`.
- `change_status` (core leads): `project.pipeline_updated` then `project.forecast_updated`.
- `ScoringService.regenerate` (ml): `signal.scored` stream entry after `lead.score_updated`.
- `libs/domain/event_chain.py`: new pure `reference_parent(event_type, key, source_version)`.
- With the flag off no code path changes the SQL or payload of existing events (tested: outbox types and payload keys, stream content).
- With the flag on the relay publishes all chain events, the Pulse and ML consumers accept them and nothing dead-letters (tested offline).

## Commands
- venv Python 3.12, `pip install '.[dev,streamlit,identity,sqltest]'`: OK (PyPI reachable).
- The four acceptance test files, `make static`, `make test` (1499 passed, 2 skipped, 4 xfailed; includes test_sql_consistency and test_openapi): pass.

## Assumptions
- `funnel.gate_decision` already has its producer (`funnel.py`, out of scope); the "six" are tagged, scored, matched, explained, pipeline_updated, forecast_updated.
- Tagged payload is `{signal_id, company_id, tags{source, signal_type}}`; the explained payload carries `qualification_reason`. Neither is specified anywhere.
- Lead stage change stands for the pipeline and forecast updates (there is no project forecast model yet).

## Unresolved risks
- Steps run in different services/transactions, so the strict parent chain cannot be real: segments created->tagged, matched->explained, pipeline->forecast are linked; the joins (tagged->scored, scored->matched, gate_decision->pipeline) use deterministic reference ids that no event carries. `trace_chain` reports those gaps. Closing them needs the parent event id plumbed through `ScoreFeatures` and the lead payload (contract change, out of scope).
- The ML `signal.scored` entry goes straight to the stream (as `lead.score_updated` does), not through the outbox.
- New SQL (`insert_chain_events`) passed the consistency test but ran only on fakes; the first real run is the CI e2e stage, which runs with the flag off.
- `SqlLeadTransaction` gained `add_chain_events`; the `LeadTransaction` protocol requires it, so any new fake of it needs the method.
