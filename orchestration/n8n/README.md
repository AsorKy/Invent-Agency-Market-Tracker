# n8n Workflows

n8n only orchestrates triggers, delivery, and notifications. Domain logic lives
in the services, dbt, and `libs/domain`.

The JSON files in this directory are importable reference workflows. Configure
Bright Data credentials and the gateway endpoint inside n8n; the files contain
no tokens.

Source workflows (`wf_maps_medellin`, `wf_computrabajo_tech`) follow one chain:
schedule, `GET {INGEST_URL}/internal/ingest/budget/{source}` (the HTTP node fails
on a non-2xx answer, which stops the run), Bright Data trigger with
`notify={INGEST_WEBHOOK_URL}`, and a Telegram notification. An error trigger
sends a failure notification. Credentials are n8n credential names
(`ingest-internal-header`, `brightdata-api-header`); nothing secret is stored.
Each file carries `versionId` and `meta.payloadVersion`.
