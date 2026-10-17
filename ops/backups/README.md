# Backups

The production flow must run `pg_dump --format=custom` per schema, verify the
restore in ephemeral Postgres, calculate SHA-256, and encrypt with `age` before
uploading the artifact. Current scripts define the execution interface and do
not operate on a database without explicit credentials.

The GitHub Actions workflow is manual during F0. Enable the nightly schedule
after connecting the runner to Supabase, `pg_dump`, ephemeral Postgres, and the
`age` key; this prevents CI from reporting a scaffold manifest as a real backup.

Target RPO/RTO from ARCH-0001: `core` 24 hours and under 15 minutes;
`analytics` and `ml` can be rebuilt from Core; `socioeconomic` 7 days.

