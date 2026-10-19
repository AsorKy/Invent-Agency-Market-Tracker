# Run report r1-d2-mlpulse

Branch: `agent/r1-d2-mlpulse`. This replaces an earlier report on the same branch that found no tasks, because that run used an older TASKS.md.

## Tasks
- T-121 done: ML publishes `lead.score_updated` in the core `stream_fields` envelope with `payload` and `tenant_id`. Pulse dead-letters malformed entries to `invent.events.dead`, acks them, and does not raise. The D-4 xfail is removed.
- T-088 done: `city`, `service_line` and `hot` are added to signal and lead event payloads. Pulse already read them.
- T-124 done: pulse returns 503 on database errors, relay/pulse/ml workers register shutdown hooks, `request_id` travels in stream entries and is bound to consumer logs, and `SHUTDOWN_GRACE_SECONDS` is in `.env.example`.
- Blocked: none.

## Commands
- Setup: python3.12 venv with `pip install '.[dev,streamlit,identity]'` succeeded.
- After each task `make static` and `make test` passed. Final `make test`: 918 passed, 2 skipped, 7 xfailed.
- `tests/unit/test_sql_consistency.py` passed after T-088 (SQL changed).
- `python ops/check_configuration.py` OK (87 variables).

## Assumptions
- ML called without `X-Tenant-Id` publishes without `tenant_id`; pulse then dead-letters it. A stricter option is to refuse to publish.
- The dead letter stream name `invent.events.dead` is our choice; SPEC gives none.
- The remote branch already held a stale report commit, so I merged it instead of force-pushing. I pushed with `git push`, not the GitHub tools.

## Changed files
services/ml/app/{scoring_service,main,consumer}.py; services/pulse/app/{consumer,main}.py; services/core/app/{event_dimensions,actions,leads,repository,dead_letter}.py; .env.example; TASKS.md; tests/unit/{test_score_event,test_event_dimensions,test_resilience_gaps,test_pipeline_flow}.py.

## Unresolved risks
- The signal `city`/`service_line` SQL merge in `repository.py` is not run against Postgres. Only the static SQL consistency test covers it.
- `tests/unit/support_relay.py` (qa scope) does not simulate the SQL-derived dimensions.
- Consumer loops (SIGTERM and ack) have no end-to-end test because there is no Redis in the sandbox.
- ML score events carry no `request_id`, because `regenerate` takes no request id.
