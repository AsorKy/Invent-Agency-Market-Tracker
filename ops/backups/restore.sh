#!/usr/bin/env sh
### Restore a schema dump after operator confirmation.
### Relates to backup.py, verify_restore.py, and backups.yml; preserves the
### runbook interface until pg_restore and secure credentials are connected.
set -eu

schema="${1:?schema is required}"
dump="${2:?dump path is required}"

printf 'Restore schema %s from %s after operator confirmation.\n' "$schema" "$dump"
printf 'The production runbook must supply pg_restore credentials outside the repo.\n'

