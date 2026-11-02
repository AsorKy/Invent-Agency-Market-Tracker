# Bright Data import: `make bd-import`

This runbook is the operator procedure for `ops/bd_import.py`, the one-shot importer of Bright Data companies, job postings, and decision makers for Medellin, Bogota, Cali, Barranquilla, and Cartagena. It lists variable names only; never write a secret into this file or into a command line.

## 1. What it does

| Phase | Source name | Endpoint | Client | Landing |
| --- | --- | --- | --- | --- |
| 0 | all five | one call per endpoint, 3 records or fewer | SDK and REST | none; redacted samples in `tests/fixtures/brightdata/recorded/` |
| 1 | `maps` | Google Maps discover by location, dataset `gd_m8ebnr0q2qlklc02fz`, 6 keywords x 40 per city | REST (`BrightDataConnector`) | `persist_snapshot` |
| 1 | `linkedin_jobs` | LinkedIn jobs discover by keyword, dataset `gd_lpfll7v5hcqtkxl6l`, 3 keywords x 50 per city | `brightdata-sdk` 2.5.2 | `persist_snapshot` |
| 1 | `business_search_companies` | `POST /search/company`, ludicrous, 11-500 employees, 2 niches per city, 100 per search | REST | `persist_snapshot` under the routing id `bd_search_company` |
| 2 | `linkedin_companies` | LinkedIn company profiles, dataset `gd_l1vikfnt1wgvvqz95w`, unique company URLs of the landed jobs (cap 300) | `brightdata-sdk` | `persist_snapshot` |
| 3 | `business_search_people` | `POST /search/people` for the 150 best scored imported companies | REST | core `POST /internal/core/signals`, `signal_type` `decision_maker` |

Phases 1 and 2 enter through `IngestRepository.persist_snapshot`, the poller's path, so the processor, resolution, core, ml, pulse, and brief run unchanged. The processor routes a snapshot to its mapping by dataset id (`dataset_sources` in `services/ingest/app/source_config.py`): the `SOURCE_<NAME>_DATASET_ID` of every enabled source, plus the fixed id `bd_search_company` for `business_search_companies`, which has no settings slot. Phase 3 knows the company id from core's lead list, so people never go through mapping or resolution. Only name, title, and the public profile (or the public profile id) of a person are kept; e-mail and phone are never copied (INV-12). Leads are read as the local caller `verify_live` (core accepts only the gateway and that caller on `/internal/core/leads`, and only outside production); signals are written as `ingest`.

Phase 3 searches only companies that this importer landed (their folded names are read back from `ingest.raw_snapshot`), never the demo seed.

## 2. Budget

Before every paid call the importer books `records requested x price` and stops that call if the run cap or the source cap would be passed; nothing is spent in that case.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BD_IMPORT_MAX_USD` | 25 | hard cap of one run (phase 0 is capped at 2 whatever the value) |
| `BD_IMPORT_MAX_USD_MAPS` | 5 | cap of the `maps` source |
| `BD_IMPORT_MAX_USD_LINKEDIN_JOBS` | 4 | cap of `linkedin_jobs` |
| `BD_IMPORT_MAX_USD_LINKEDIN_COMPANIES` | 2 | cap of `linkedin_companies` |
| `BD_IMPORT_MAX_USD_BUSINESS_SEARCH_COMPANIES` | 1 | cap of `business_search_companies` |
| `BD_IMPORT_MAX_USD_BUSINESS_SEARCH_PEOPLE` | 1 | cap of `business_search_people` |

Price table (`PRICES` in `ops/bd_import.py`):

| Source | Price used | Status |
| --- | --- | --- |
| `maps`, `linkedin_jobs`, `linkedin_companies` | $2.00 per 1,000 records | not confirmed per scraper: the public pay-as-you-go rate is $1.50 per 1,000 records and no per-scraper rate is published, so the SDK 2.5.2 LinkedIn constant is used as a conservative value |
| `business_search_companies`, `business_search_people` | $2 per 1,000 searches, one search is a block of up to 100 requested results | confirmed in the Business Search pricing page |

The estimate of a full run (`--phase all`, five cities) is $4.82: maps $2.40, jobs $1.50, companies $0.60, company searches $0.02, people searches $0.30. Every run prints the cumulative estimate and an end-of-run report per source: records, share with phone or website, share of mid-size companies (11 to 500 employees), share of searched companies with at least one decision maker, and estimated cost.

## 3. Before the first bulk run (phases 1 to 3)

1. `.env` must hold exactly one `BRIGHT_DATA_API_TOKEN=` line with the token. A second, empty line later in the file wins and leaves the token unset (the importer then stops with "BRIGHT_DATA_API_TOKEN is not set").
2. Routing: set these in `.env` so the processor maps each landed dataset to its source (the importer refuses to land and prints the missing lines otherwise):
   - `SOURCE_MAPS_ENABLED=true` and `SOURCE_MAPS_DATASET_ID=gd_m8ebnr0q2qlklc02fz`
   - `SOURCE_LINKEDIN_JOBS_ENABLED=true` and `SOURCE_LINKEDIN_JOBS_DATASET_ID=gd_lpfll7v5hcqtkxl6l`
   - `SOURCE_LINKEDIN_COMPANIES_ENABLED=true` and `SOURCE_LINKEDIN_COMPANIES_DATASET_ID=gd_l1vikfnt1wgvvqz95w`
   `business_search_companies` needs no line: its routing id is fixed in code.
3. Rebuild and recreate the processor so it runs the new mapping files, the `city` transform, and the routing: `docker compose up -d --build ingest-processor`. The images copy the code at build time, so a processor that was not rebuilt still uses the old mappings and drops Business Search snapshots ("no mapping source for dataset").
4. Plan without spending: `make bd-import PHASE=all ARGS=--dry-run`.

## 4. Commands

The target builds the `bd-import` tool image (profile `tools`, extra `brightdata`) and runs it inside the Compose network, so it reaches Postgres as `ingest_app`, Redis, and core by service name (`BD_IMPORT_CORE_URL`). The running stack is not stopped or rebuilt. Outside Compose, `python ops/bd_import.py` needs `pip install '.[brightdata]'` and reachable `DATABASE_URL`, `REDIS_URL`, and `CORE_URL`; phase 0 needs none of the three.

- Probe: `make bd-import PHASE=0`
- Bulk: `make bd-import PHASE=1`, then `make bd-import PHASE=2`, then `make bd-import PHASE=3` (or `PHASE=all`)
- One city: `make bd-import PHASE=1 CITIES=Medellin`
- Lower cap for one run: `make bd-import PHASE=1 ARGS="--max-usd 5"`

Phase 2 needs the jobs of phase 1 in `ingest.raw_snapshot`; phase 3 needs scored leads in core, so wait until ml has scored the imported companies (pulse and the lead list show scores) before running it.

## 5. Idempotency and re-runs

The ledger (`BD_IMPORT_STATE_DIR`, the `bd_import_state` volume in Compose) keeps the key of every call that landed; a re-run skips those calls and spends nothing on them. `persist_snapshot` ignores a snapshot it already stored, core upserts are keyed by content, and every decision maker signal carries an idempotency key and a `source_external_id` derived from tenant, company, and profile. A call that was paid but failed to land is not in the ledger and is called again on the next run. To start over, remove the volume: `docker volume rm` of the `bd_import_state` volume of the project.

## 6. Mapping verification (decision D-13)

Phase 0 writes one redacted sample per endpoint to `tests/fixtures/brightdata/recorded/<source>.json`: person values (and e-mail addresses in free text) become placeholders of the same type, company data stays, and no request header is stored. A sample made only of provider error records is not a sample: keep it under `recorded/errors/`. Check the mappings with `python ops/verify_mappings.py --samples tests/fixtures/brightdata/recorded --source linkedin_jobs --source linkedin_companies`; `--apply` sets `unverified: false` for a source with enough coverage. The unit tests assert that a mapping is `unverified` exactly when no real recorded or captured sample of its source exists.

## 7. Phase 0 results (2026-11-02)

| Endpoint | Result | Mapping |
| --- | --- | --- |
| LinkedIn jobs (SDK) | 3 records, `limit_per_input` held; keyword returned in `discovery_input` | `linkedin_jobs` v2, verified (coverage 1.00) |
| LinkedIn companies (SDK) | 3 records from the jobs' `company_url`; 1 of 3 mid-size | `linkedin_companies` v2, verified (coverage 0.85) |
| Google Maps discover by location (REST) | `crawl_error` on 3 attempts (array and wrapped body, two keywords): a provider-side parse failure, no place record | `maps` v2 known fixes, still unverified |
| Business Search companies and people (REST) | HTTP 403: the account is not on the Business Search allowlist (early access); not billed | `business_search_companies` v1, unverified |

Until Business Search access is granted, phase 1 lands no company search and phase 3 finds no decision maker; until the Maps scraper works, phase 1 lands no place. Provider error records are never landed and are not billed (pay only for success).

## 8. Troubleshooting

- `BudgetExceededError` in the report: the call was not made. Raise `BD_IMPORT_MAX_USD` or the source cap only after checking the plan with `--dry-run`.
- Snapshots land but no company appears: check `ingest.failed_record` for "no mapping source for dataset" (routing, section 3) and that the processor was rebuilt.
- 403 on `/search/company`: the account is not on the Business Search allowlist (early access).
- Phase 3 finds no companies: the imported companies have no scored lead yet, or their names differ from the lead's legal name.
