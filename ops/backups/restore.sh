#!/usr/bin/env sh
### Restore one schema dump after the operator types the schema name twice.
### Relates to backup.py, verify_restore.py, README.md, and backups.yml.
### Decrypt first on the operator machine: age -d -i <private key file> -o x.dump x.dump.age
### The target is read from PGDATABASE; never pass a connection string as an argument.
set -eu

schema="${1:?schema is required}"
dump="${2:?dump path is required}"
: "${PGDATABASE:?PGDATABASE must name the target database}"

case "$schema" in
  identity|ingest|core|pulse|brief|ml|socio) ;;
  *) printf 'Unknown schema %s\n' "$schema" >&2; exit 2 ;;
esac
[ -f "$dump" ] || { printf 'Dump not found: %s\n' "$dump" >&2; exit 2; }

printf 'Type the schema name to restore it from %s: ' "$dump"
read -r first
printf 'Type it again: '
read -r second
if [ "$first" != "$schema" ] || [ "$second" != "$schema" ]; then
  printf 'Confirmation mismatch; nothing restored.\n' >&2
  exit 1
fi

pg_restore --clean --if-exists --exit-on-error --dbname "$PGDATABASE" "$dump"
printf 'Restored %s. Run the smoke checks before starting services.\n' "$schema"
