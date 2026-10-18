# Source mapping files

One YAML file per source: `<source>.yaml` (REQ-ING-004). A provider field change
is a file edit, not a code change. Loader and engine: `services/ingest/app/mapping.py`.

Format (`version: 1`):

- `source`: source name, equal to the file name.
- `version`: integer, bump on every change.
- `unverified`: `true` until the mapping is checked against a recorded provider
  sample under `tests/fixtures/brightdata/` (decision D-13).
- `entity`: `company` or `job_posting`.
- `signal_type`: signal type created for a record of this source.
- `external_id`: list of provider paths; the first non-empty value is the id.
- `fields`: canonical field name -> rule with
  - `from`: list of provider paths (dotted, first non-empty wins),
  - `transform`: optional, one of `strip`, `lower`, `int`, `float`, `digits`, `url`,
  - `required`: optional bool, a missing required field rejects the record,
  - `default`: optional value used when no path yields a value.
