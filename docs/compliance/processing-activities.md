# Record of processing activities (RAT)

Status: minimum record required by REQ-CMP-001. The formal decision record
(ADR-IMT-0006-HABEAS) is pending; this file is the working register until it exists. It applies
to Colombian Law 1581 of 2012 (Habeas Data) and Decree 1377 of 2013.

## Controller and purpose

| Item | Value |
| --- | --- |
| Controller | The agency operating the Invent Market Tracker instance (one entry per tenant) |
| Purpose | Evaluate Colombian market opportunities and manage commercial leads |
| Legal basis | Documented legitimate interest in business-to-business prospecting |
| Data subjects | Companies; people only in a public business role |

## Data processed

| Category | Fields | Where |
| --- | --- | --- |
| Company | NIT, legal and trade name, sector, size, website, city | `core.company`, `core.location` |
| Public signals | Source, type, observation time, strength | `core.signal` |
| Business contacts | Name and public role title only (CEO, manager, director) | `core.person_role` |
| Commercial activity | Lead stage, outcomes, activities, projects | `core.lead`, `core.lead_outcome`, `core.lead_activity`, `core.project` |

## Never stored

Identity document numbers (cedula), private or personal phone numbers, residential addresses,
data about minors, and sensitive data (health, sexual orientation). The Core API rejects payloads
carrying such fields or values (`reject_personal_identifiers`) and only accepts public business
roles (`ensure_public_role`) in `services/core/app/retention.py`.

## Retention

- Companies with no signal for 12 months are deleted together with their leads, outcomes,
  activities, signals, locations, roles, and company history.
- The purge is `POST /internal/core/compliance/purge?dry_run=false` or
  `python -m services.core.app.retention`; both support a dry run (the default for the endpoint).
  A scheduler runs it. Both ask the identity service for the active tenant ids and purge each
  tenant in its own transaction under its own tenant context; a tenant that fails does not stop
  the others and the job exit code reports it. Each tenant writes a `retention.purge` audit entry
  with its `tenant_id` and counts only.
- The outbox event log keeps events for 90 days (see ADR-IMT-0001, section E1.4).

## Data subject requests (opt-out)

1. The request arrives through the public form or the legal inbox.
2. An operator calls `POST /internal/core/compliance/opt-out` with the company id or NIT and the
   `X-Tenant-Id` header (400 without it). The request only reaches companies of that tenant; the
   same company in two tenants needs two requests. An admin user can send the same request from the
   app with `POST /api/v1/compliance/opt-out`; the gateway takes the tenant from the JWT only.
3. Core deletes the company and its dependants in one transaction. Repeating the call is safe and
   returns `already_removed`.
4. The audit log keeps only a SHA-256 of the identifier, the channel, row counts, and the tenant id.

## Registration

If stored records of natural persons exceed 10,000, the database must be registered in the
Registro Nacional de Bases de Datos (SIC).

## Open items

- Suppression list so an opted-out company is not re-ingested from a source.
- Scrubbing company data copied into outbox event payloads on opt-out.
