# Run report r1-c-ingest

Branch: `agent/r1-c-ingest`. Setup: python3.12 venv, `pip install '.[dev,streamlit]'` succeeded.

## Tasks
| Task | Status | Evidence |
| --- | --- | --- |
| T-045 Processor to core flow | done | `pytest -q tests/unit/test_ingest_to_core.py` 11 passed |
| T-046 Enrichment agent contract | done | `pytest -q tests/unit/test_enrichment.py` 7 passed |
| T-048 LinkedIn and Instagram sources | done | `pytest -q tests/unit/test_connectors_more.py` 13 passed |
| T-096 Mapping verification harness | done | `pytest -q tests/unit/test_verify_mappings.py` 10 passed |

Blocked: none. `make static` and `make test` passed before every commit (final: 582 passed, 2 skipped, 4 xfailed).

## Changed files
- T-045: `services/ingest/app/to_core.py` (new), `repository.py`, `processor.py`, `tests/unit/test_ingest_to_core.py`
- T-046: `services/ingest/app/enrichment.py`, `tests/unit/test_enrichment.py`
- T-048: `libs/domain/source_mappings/{linkedin_companies,linkedin_jobs,instagram}.yaml`, `tests/unit/test_connectors_more.py`, `tests/unit/test_source_config.py` (outside scope by one test: instagram now has a mapping)
- T-096: `ops/verify_mappings.py`, `tests/fixtures/brightdata/synthetic/*.json`, `tests/unit/test_verify_mappings.py`, `tests/unit/test_connectors.py` (has_samples check now looks at `brightdata/captured/`, since `synthetic/` must not count as verification)
- `TASKS.md` (own task blocks only), this report

## Assumptions
- `libs/contracts/companies.py` and `services/ingest/app/resolution.py` do not exist; T-045 used core's upsert/signal shapes as dicts and added `SqlResolutionStore` in `to_core.py`.
- The processor wires `MockRuesLookup([])`: every candidate resolves as `new_company` without NIT until a real RUES lookup exists.
- LinkedIn/Instagram field names come from memory of public Bright Data datasets, not fetched docs. All mappings remain `unverified: true`.
- Only five mappings exist (task text said six).

## Unresolved risks
- SQL added in T-045 was not run against a real database; `tests/unit/test_sql_consistency.py` does not exist yet. ON CONFLICT targets are backed by existing unique indexes.
- No consumer reprocesses `ingest.rues_lookup_queue`.
- Enrichment (T-046) is in-memory: no migration for an enrichment table/daily usage (database-engineer), Brief does not yet call `brief_warning` (`enrichment_pending_retry`) (lead-backend-engineer), no gateway route.
- `--capture` redaction covers only what `redact_text` handles (documents, emails); owner should review captured files before committing. `verify_mappings.py` imports private helpers from `services/ingest/app/mapping.py`; `--apply` flips on coverage alone.
- `services/mocks` does not emit source-specific records for the new sources.
- Core calls happen inside the per-record DB transaction (pool_size=2).
