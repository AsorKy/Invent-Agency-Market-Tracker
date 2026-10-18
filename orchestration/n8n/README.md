# n8n Workflows

n8n only orchestrates triggers, delivery, and notifications. Domain logic lives
in the services, dbt, and `libs/domain`.

The JSON files in this directory are importable reference workflows. Configure
Bright Data credentials and the gateway endpoint inside n8n; the files contain
no tokens.

Source workflows (`wf_maps_medellin`, `wf_computrabajo_tech`,
`wf_linkedin_companies_backfill`, `wf_linkedin_jobs`, `wf_instagram_horeca`) follow one chain:
schedule, `GET {INGEST_URL}/internal/ingest/budget/{source}` (the HTTP node fails
on a non-2xx answer, which stops the run), Bright Data trigger with
`notify={INGEST_WEBHOOK_URL}`, and a Telegram notification. An error trigger
sends a failure notification. Credentials are n8n credential names
(`ingest-internal-header`, `brightdata-api-header`); nothing secret is stored.
Each file carries `versionId` and `meta.payloadVersion`.

Other workflows:

| File | Trigger | Behavior |
|---|---|---|
| `wf_rues_delta` | weekly | budget check, `POST {RUES_DELTA_URL}` with `notify={INGEST_WEBHOOK_URL}`, Telegram |
| `wf_socioeco_refresh` | monthly | `POST {ANALYTICS_URL}/internal/analytics/socioeconomic/refresh`, Telegram |
| `wf_hot_lead_alert` | webhook `hot-lead` | forwards the core alert body (`lead_id`, `company_id`, `tenant_id`, `score`, which may be null) to Telegram |
| `wf_ops_health` | hourly | `GET /health` of core and ingest; an error trigger sends an email (SMTP credential `ops-smtp`, `OPS_ALERT_FROM`, `OPS_ALERT_TO`) |

Schedules: linkedin companies monthly, linkedin jobs weekly, instagram weekly, RUES
weekly, socioeconomic monthly, ops health hourly.
