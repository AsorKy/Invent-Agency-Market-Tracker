# RUN r1-h-scores

Branch: agent/r1-h-scores. Tasks: T-144 (done), T-145 (done).

## T-144 core serves score weights to ML
- Core: `GET /internal/core/admin/score-rules` (module `score_rules.py`), service `ml` only, `set_tenant_context` inside the transaction, `version` = crc32 of the rules (0 when empty).
- ML: `core_rules.py` (`HttpCoreRules`, 2 s timeout, signed token, `X-Tenant-Id`, `CoreRulesUnavailable`); `load_weights` returns None (default weights, warning) when core fails. Direct SQL on `core.lead_score_rule` removed.
- Route added to NOT_PUBLIC; docs/api/openapi.json regenerated.

## T-145 core stores the score
- Migration `20261022_0012_core_company_score.sql`: nullable `score` (0..100), `score_source_version`, `score_updated_at` on `core.company`; partial index; existing RLS policy applies.
- Worker stores the score on `lead.score_updated` before auto-qualification (separate idempotent transaction); never overwrites a newer numeric `source_version`.
- Lead list, `get_lead`, brief-facts and `min_score` read `core.company.score`. `list_leads` now calls `set_tenant_context`.
- table_catalog snapshot regenerated (+3 columns).

## Commands
- `make static`, `make test` (T-144: 1440 passed; T-145: 1446 passed, 2 skipped, 4 xfailed), `python ops/check_migrations.py` OK, targeted pytest lists green, test_sql_consistency green.

## Assumptions
- Token of a service outside the allow list gets 401 (library behaviour), not 403 as the task text says; tests assert 401.
- A tenant with no rules yields an empty list; ML then reports `rules_source` tenant with default weights.
- `test_cov_infra.py` worker-main test adjusted for the new `build_handlers` signature (slightly beyond the lead filter tests).

## Unresolved risks
- docker-compose `ml` service has no `CORE_URL`; ML defaults to http://localhost:8002, so in the default stack it falls back to default weights with a warning. Needs `CORE_URL: http://core:8000` on `ml` (devops task; not changed here per the no-stack-change rule). Also check ml has the internal service token env.
- Events without `tenant_id` only update companies with NULL tenant.
- RLS isolation of the new columns not run against real Postgres (no Docker).
- Stray ruff-format changes in unrelated tests by T-144 were reverted.
