# Go-live runbook: from mock data to live sources

This runbook switches a local stack from mock data to live Bright Data sources and shows how to check that data reaches the public API. It satisfies REQ-DOC-001. It lists variable names only. Never write a secret into this file, into a commit, or into a log. Only `.env.example` is committed; `.env` stays local and ignored by git.

## 1. Prerequisites

- Docker with Compose, GNU make, and Python 3.12 with the project installed (`pip install .[dev]`).
- A Bright Data account with an API token and one dataset ID per source you want to enable.
- A way to receive the Bright Data webhook (section 4), or acceptance that polling alone will deliver snapshots.

## 2. Start the stack in mock mode first

1. Run `make invent-tracker`. It creates `.env` from `.env.example` (generating passwords), starts Postgres and Redis, creates the per-service database roles, builds and starts all services, waits for readiness, and loads demo data once (marker file `.env.seeded`). It is safe to run again.
2. Run `make doctor` and confirm that nothing required is missing (section 6).
3. Stop with `make invent-tracker-down`. `make invent-tracker-reset` also deletes the data volumes and asks for confirmation.

## 3. Credentials and enabling sources

Put these values in `.env` (never in `.env.example`). Names below are the ones used by the code.

| Variable | Purpose |
| --- | --- |
| `INGEST_MODE` | `mock` (default) uses `services/mocks`; `live` calls Bright Data. |
| `BRIGHT_DATA_API_URL` | Bright Data API base URL; the default is correct. |
| `BRIGHT_DATA_API_TOKEN` | API token. Required when `INGEST_MODE=live`. |
| `BRIGHT_DATA_WEBHOOK_SECRET` | Shared secret used to verify webhook signatures. Required when `INGEST_MODE=live`. |
| `BRIGHT_DATA_REPLAY_WINDOW_SECONDS` | Accepted age of a signed webhook (default 300). |
| `SOURCE_<NAME>_ENABLED` | Switch for one source. |
| `SOURCE_<NAME>_DATASET_ID` | Bright Data dataset ID of that source. Required for every enabled source in live mode. |

Sources and their switches:

| Source | Enable flag | Dataset ID | Default |
| --- | --- | --- | --- |
| Google Maps | `SOURCE_MAPS_ENABLED` | `SOURCE_MAPS_DATASET_ID` | on |
| Computrabajo | `SOURCE_COMPUTRABAJO_ENABLED` | `SOURCE_COMPUTRABAJO_DATASET_ID` | on |
| LinkedIn companies | `SOURCE_LINKEDIN_COMPANIES_ENABLED` | `SOURCE_LINKEDIN_COMPANIES_DATASET_ID` | off |
| LinkedIn jobs | `SOURCE_LINKEDIN_JOBS_ENABLED` | `SOURCE_LINKEDIN_JOBS_DATASET_ID` | off |
| Instagram | `SOURCE_INSTAGRAM_ENABLED` | `SOURCE_INSTAGRAM_DATASET_ID` | off |
| RUES | `SOURCE_RUES_ENABLED` | `SOURCE_RUES_DATASET_ID` | off |

Steps:

1. Set the token and the webhook secret.
2. Set the dataset ID of each source you will use, and set its enable flag to `true`; disable the others with `false`. Enable one source first and verify it before adding more.
3. Set `INGEST_MODE=live`.
4. Recreate the services so they read the new values: `docker compose up -d --build`. In live mode the ingest service fails at startup and names each missing variable (never its value). Read that message with `docker compose logs ingest`.

`make verify-live` accepts maps, computrabajo, linkedin_companies, linkedin_jobs, and instagram. RUES is not one of its sources.

## 4. Webhook tunnel or polling fallback

Bright Data delivers finished snapshots by webhook. The stack can also find them by polling, so you can go live without a public URL.

### Option A: webhook through a tunnel

1. Start a tunnel tool of your choice that forwards a public HTTPS URL to `http://localhost:8000` (the gateway; the port is `GATEWAY_PORT`).
2. Set `PUBLIC_WEBHOOK_BASE_URL` to the public base URL of the tunnel.
3. In Bright Data, configure the delivery webhook as `<PUBLIC_WEBHOOK_BASE_URL>/api/v1/ingest/webhook/bd` and use the same secret as `BRIGHT_DATA_WEBHOOK_SECRET`.
4. The ingest service accepts a request only with a valid `X-BrightData-Signature` and a fresh `X-BrightData-Timestamp` (within `BRIGHT_DATA_REPLAY_WINDOW_SECONDS`). A repeated snapshot is reported as a duplicate and stored once.

`INGEST_WEBHOOK_URL` is the internal address used by the mocks and n8n; leave its default.

### Option B: polling fallback

1. Leave `PUBLIC_WEBHOOK_BASE_URL` empty.
2. Keep `INGEST_POLL_ENABLED=true`. The ingest service tracks every triggered collection and asks Bright Data for its status every `INGEST_POLL_INTERVAL_SECONDS` (default 300) until the snapshot is ready, then stores it. If the webhook also arrives, the snapshot is stored once.
3. Expect a delay of up to one poll interval. Lower the interval while testing.

## 5. Adjust field mappings

A provider field change is a file edit, not a code change. Mapping files live in `libs/domain/source_mappings/<source>.yaml`; the format is in `libs/domain/source_mappings/README.md`.

1. Run `make verify-live` for the source (section 7). After the first snapshot reaches the `api_read` hop it stores redacted real records in `tests/fixtures/brightdata/captured/` and prints the mapping coverage.
2. If coverage is low, or fields are reported as unused or missing, edit the `from` paths of the mapping file and raise its `version` by one.
3. Re-check against the captured records: `python ops/verify_mappings.py --samples tests/fixtures/brightdata/captured --source <source>`. The default minimum coverage is 0.8 (`--min-coverage`).
4. Mappings of `linkedin_jobs` and `instagram` ship with `unverified: true`: their field names come from public documentation, not from a recorded sample. After a real capture that meets coverage, run the same command with `--apply` to flip the flag to `false`. `--apply` refuses a source whose samples are synthetic (records with `"_synthetic": true` under `tests/fixtures/brightdata/synthetic/` only test the tool). Review the diff and commit it.
5. Restart the ingest service (`docker compose restart ingest`) so it loads the edited file.

Treat any mapping still marked `unverified: true` as not proven against live data.

## 6. Run `make doctor`

`make doctor` reads `.env` and the environment and prints, by name only:

- which required variables are set or missing (passwords, `INTERNAL_SERVICE_TOKEN`, role passwords);
- which live variables are missing (`BRIGHT_DATA_API_TOKEN`, `BRIGHT_DATA_WEBHOOK_SECRET`);
- which sources are active and in which mode;
- the health of each service as seen through the gateway readiness check;
- whether the configured webhook URLs answer.

Values and URLs are never printed. Exit code 1 means a required variable is missing. Fix every item before continuing.

## 7. Run `make verify-live`

```text
make verify-live SOURCE=maps INPUTS=inputs.json
```

- `SOURCE` is one source name from section 3. `INPUTS` is a JSON list of provider inputs (for example search terms or URLs, as the dataset expects), with at most 5 entries. This keeps the cost of a check bounded.
- It refuses to run unless `INGEST_MODE=live` and the source is enabled.
- It triggers one collection, then follows the data through the hops `trigger`, `snapshot`, `normalize`, `resolve`, `signal`, `score`, and `api_read`. Each hop is polled until a timeout (`--timeout`, 300 seconds by default). The first failing hop stops the run, later hops show as skipped, and the failure prints the next step.
- When every hop passes it captures redacted fixtures and prints mapping coverage (section 5).
- Exit code 0 means all hops passed, 1 a hop failed, 2 a refusal or usage error.

Known caveat: Compose does not publish the ingest and core ports, so the defaults (`http://localhost:8001` and `http://localhost:8002`) do not answer. Set `VERIFY_LIVE_INGEST_URL` and `VERIFY_LIVE_CORE_URL` to addresses that reach those services, for example through a local override file that publishes the ports, or run the command from inside the Compose network. `VERIFY_LIVE_DATABASE_URL` overrides the database connection; otherwise `DATABASE_URL` or the `POSTGRES_*` values are used. These three `VERIFY_LIVE_*` names are read only by `ops/verify_live.py` and are not listed in `.env.example`.

## 8. How data reaches `/api/v1`

1. Trigger: the ingest service starts a collection at Bright Data (or n8n schedules it) and records it as pending.
2. Snapshot: the finished snapshot arrives by webhook or by polling and is stored once, keyed by its payload hash.
3. Normalize: the mapping file of the source turns provider records into canonical fields. Records that fail a required field go to the failed-record table.
4. Resolve: entity resolution matches or creates the company.
5. Signal: ingest delivers the signal to the core service, authenticated with `INTERNAL_SERVICE_TOKEN`.
6. Score: the ml service scores the company and the lead appears with a score.
7. Read: the gateway serves the data under `/api/v1` (for example `/api/v1/leads`, `/api/v1/companies`, `/api/v1/signals`, `/api/v1/scores`, and the `/api/v1/pulse/...` routes). The contract is `docs/api/openapi.json`.

Only the gateway is meant to be called by clients; the services behind it are internal.

## 9. Point the frontend at the gateway

The frontend is a separate application. With the local profile:

1. Keep `APP_PROFILE=local`. In this profile the gateway allows the origin in `FRONTEND_ORIGIN` with credentials (CORS). Other profiles do not.
2. Set `FRONTEND_ORIGIN` to the exact origin of the frontend dev server (default `http://localhost:3000`) and recreate the gateway.
3. Configure the frontend API base URL as `http://localhost:8000/api/v1` (use `GATEWAY_PORT` if you changed the port).
4. Sign in with `POST /api/v1/auth/session`. The development users and their generated passwords are in `.env` (`DEV_USER_PASSWORD_ADMIN`, `DEV_USER_PASSWORD_STRATEGIST`, `DEV_USER_PASSWORD_INTAKE`, `DEV_USER_PASSWORD_BD_REP`, `DEV_USER_PASSWORD_ANALYST`). Send requests with credentials so the session cookie is kept. When `CSRF_REQUIRED` is true, state-changing requests also need the `X-CSRF-Token` header.
5. Check with `GET /api/v1/me` and `GET /api/v1/leads`.

## 10. Troubleshooting

| Symptom | Likely cause | Check or fix |
| --- | --- | --- |
| Ingest container exits at startup with "INGEST_MODE=live requires" | A live variable is empty | Set the variables named in the message (token, webhook secret, dataset IDs of enabled sources) and recreate. |
| `make doctor` exits 1 | A required variable is missing | Run `make invent-tracker` to generate passwords, or fill the named variable in `.env`. |
| `make verify-live` exits 2 | Not in live mode, source disabled, unknown source, or more than 5 inputs | Fix `INGEST_MODE`, the enable flag, or the inputs file. |
| `trigger` hop fails, or connection refused to port 8001 or 8002 | Wrong dataset ID or token, exhausted budget, or ingest port not reachable | Run `make doctor`; set `VERIFY_LIVE_INGEST_URL` and `VERIFY_LIVE_CORE_URL`. |
| `snapshot` hop times out | Webhook not reaching the stack and polling off or slow | Check `PUBLIC_WEBHOOK_BASE_URL` and the tunnel, or `INGEST_POLL_ENABLED` and `INGEST_POLL_INTERVAL_SECONDS`. |
| Webhook answers 401 | Wrong secret, bad signature, or timestamp outside the replay window | Match `BRIGHT_DATA_WEBHOOK_SECRET` on both sides; check the host clock and `BRIGHT_DATA_REPLAY_WINDOW_SECONDS`. |
| `normalize` hop fails or coverage is low | Mapping does not match the real records | Section 5; edit the mapping and re-run `ops/verify_mappings.py`. |
| `resolve` hop fails | Records rejected during resolution | Inspect `ingest.failed_record` and `docker compose logs ingest`. |
| `signal` hop fails | Ingest cannot reach core, or tokens differ | Check `INTERNAL_SERVICE_TOKEN` and the core logs. |
| `score` hop fails | The ml service is down or has no scoring rules | Check `docker compose logs ml`. |
| `api_read` hop fails or the API returns no leads | Gateway or core unreachable, or the session is not signed in | Check `make doctor` readiness lines and sign in again. |
| Browser blocks calls to the gateway (CORS) | `FRONTEND_ORIGIN` differs from the page origin, or the profile is not local | Match the origin exactly and keep `APP_PROFILE=local`. |
| Gateway answers 429 | Rate limit reached | Raise `RATE_LIMIT_REQUESTS` or wait for `RATE_LIMIT_WINDOW_SECONDS`. |
| Edited mapping has no effect | The ingest service still runs the old file | `docker compose restart ingest`. |

Return to mock mode at any time with `INGEST_MODE=mock` and `docker compose up -d`.

## 11. Enable the nightly backups

The `backups` workflow stays disabled until its four repository secrets exist; while any is missing it skips the backup job and prints "backups disabled: secrets missing" as a warning and in the job summary. Before go-live:

1. Add the secrets `BACKUP_SOURCE_URL`, `BACKUP_EPHEMERAL_ADMIN_URL`, `BACKUP_EPHEMERAL_PASSWORD`, and `BACKUP_AGE_RECIPIENT` (names only here; never write their values in the repository).
2. Start the workflow once by hand (`gh workflow run backups`) and confirm that the job runs, the restore check passes, and the `backups-age` artifact holds only `.age` files.
3. Confirm that the next scheduled nightly run (03:17 UTC) also passes.

## 12. Create the n8n credentials for pulse and drift alerts

Create these two credentials in n8n (names only here; never write their values in the repository or in a commit):

1. `pulse-internal-header`, a header-auth credential used by the workflow `wf_socioeco_refresh` to call the pulse refresh route.
2. `drift-alert-header`, a header-auth credential used by the webhook of `wf_drift_alert`. Its header name and value must equal `DRIFT_ALERT_HEADER_NAME` and `DRIFT_ALERT_HEADER_VALUE` on the drift worker.

Owner decision needed: the n8n service tokens live 60 seconds, while a static-header credential does not expire. The socioeconomic refresh workflow therefore needs the owner to decide how it authenticates (a static header credential accepted by pulse, or a short-lived token minted before each run). Do not activate `wf_socioeco_refresh` until the owner decides. Checkable: both credentials exist in n8n and the decision is recorded.

## 13. Start the drift worker

The drift worker is outside the default stack. It is the Compose profile `lens`:

1. Set `DRIFT_ALERT_WEBHOOK_URL` to the production webhook of `wf_drift_alert` (empty means the worker only logs), and set `DRIFT_ALERT_HEADER_NAME` and `DRIFT_ALERT_HEADER_VALUE` to match the credential `drift-alert-header`.
2. Set `DRIFT_TENANT_ID` to the tenant whose series are checked, and `DRIFT_RUN_HOUR_UTC` to the hour (UTC) of the nightly run (default 7).
3. Start it with `docker compose --profile lens up -d drift-worker`, then read `docker compose logs drift-worker`. The image copies `config/` and sets `PYTHONPATH=/app`, so rebuild it (`docker compose --profile lens build drift-worker`) after any change to the thresholds.

## 14. Replace the example funnel checklists

1. `FUNNEL_ALLOW_EXAMPLE_CHECKLISTS` must stay `false` in production. With `true`, the engine loads the example checklists, which is for tests and demos only.
2. The files `config/funnel/g1_discovery.yaml` to `config/funnel/g7_sunset.yaml` are marked `example: true`. The owner replaces them with the real checklists of each gate (decision D-06) and removes the marker. Until then production refuses to load them.
3. Checkable: no file in `config/funnel/` still contains `example: true`.

## 15. Confirm the validation threshold bands

The ADR is silent on some band boundaries, so the code uses values chosen by the team. The owner confirms or changes them before real use:

1. LTV:CAC at 5.
2. Burn multiple between 2 and 3.
3. CAC payback between 12 and 18 months.
4. The `horizon_mix_deviation` bands, which are invented and have no source in the ADR.

Record the confirmed values in the run report; a change is an edit of the threshold configuration plus a rebuild of the image that reads it.
