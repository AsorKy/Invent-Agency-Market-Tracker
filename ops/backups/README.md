# Backups

Nightly workflow `.github/workflows/backups.yml` (03:17 UTC, also manual) runs
`python -m ops.backups.backup`. For every schema in `ops/schemas.py` it:

1. runs `pg_dump --format=custom --schema=<schema> --table=public.schema_migration`
   with the role `<schema>_owner`;
2. records the SHA-256 of the dump (printed in the job log);
3. restores the dump into an ephemeral Postgres database (roles bootstrapped,
   then `pg_restore --exit-on-error`) and compares the versions in
   `public.schema_migration` and the base table count of the schema with the
   source; any difference fails the job (`ops/backups/verify_restore.py`);
4. encrypts with `age --recipient $BACKUP_AGE_RECIPIENT`, then deletes the
   plaintext;
5. uploads only the `*.age` files as an artifact: `retention-days` 7 for daily
   runs and 28 for Sunday runs.

## Configuration

All values are GitHub secrets, never committed: `BACKUP_SOURCE_URL` (source
database, reachable by the owner roles), `BACKUP_EPHEMERAL_ADMIN_URL` (superuser
connection to the ephemeral Postgres service; the throwaway
`restore_<schema>_<id>` database URL is derived from it and dropped after each
check), `BACKUP_EPHEMERAL_PASSWORD` (password of the service container) and
`BACKUP_AGE_RECIPIENT` (the public `age` key). Connection strings travel to
child processes in `PGDATABASE`; only the throwaway database URL is a
`pg_restore --dbname` argument because `pg_restore` ignores `PGDATABASE`. The private `age` key
never leaves the operator machine.

## RPO and RTO per schema (ARCH-0001 section 18.2)

| Schema | RPO | RTO target |
| --- | --- | --- |
| identity | 24 h (nightly dump) | < 15 min |
| core | 24 h (nightly dump) | < 15 min |
| ingest | 24 h | < 15 min |
| pulse | 24 h | < 15 min |
| brief | 24 h | < 15 min |
| ml | 0 against core (rebuilt with `core.republish`) | < 60 min |
| analytics (legacy) | 0 against core (rebuilt with `core.republish`) | < 60 min |
| socio | 7 days (indicators change monthly) | < 15 min |

ARCH-0001 does not list `identity`, `pulse`, and `brief`; they use the 24 h and
15 min nightly values until the owner decides otherwise.

## Restore runbook

1. Download the `.age` artifact and decrypt it on your machine with your
   private key: `age -d -i <key file> -o <schema>.dump <schema>.dump.age`.
2. Stop the services that use the schema.
3. Set `PGDATABASE` to the target connection (never as an argument) and run
   `sh ops/backups/restore.sh <schema> <schema>.dump`. It asks for the schema
   name twice.
4. Start the services and run the smoke checks.

## Tests

`pytest -q tests/unit/test_backups.py` covers the orchestration with fake
`pg_dump`, `pg_restore`, `psql`, and `age` runners. No live service is used.
