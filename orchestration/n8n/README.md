# n8n Workflows

n8n only orchestrates triggers, delivery, and notifications. Domain logic lives
in the services, dbt, and `libs/domain`.

The JSON files in this directory are importable reference workflows. Configure
Bright Data credentials and the gateway endpoint inside n8n; the files contain
no tokens.

