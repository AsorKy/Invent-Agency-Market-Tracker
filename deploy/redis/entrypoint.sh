#!/bin/sh
# Render the Redis ACL file from acl.rules and the environment, then start redis-server.
# Redis ACL files cannot read environment variables, so passwords are written here at
# start. The file is private to the container; the default user is always disabled.
# Env: REDIS_ACL_RULES (rules path), REDIS_ACL_FILE (output path), plus the password
# variables named in the rules file. Extra arguments go to redis-server.
set -eu
here=$(dirname "$0")
rules=${REDIS_ACL_RULES:-$here/acl.rules}
out=${REDIS_ACL_FILE:-/tmp/users.acl}
umask 077
printf 'user default off\n' > "$out"
while read -r user var rest; do
  case "$user" in
    '' | '#'*) continue ;;
  esac
  password=$(printenv "$var" || true)
  if [ -z "$password" ]; then
    echo "redis acl: $var is not set" >&2
    exit 1
  fi
  case "$password" in
    *[[:space:]]*)
      echo "redis acl: $var must not contain whitespace" >&2
      exit 1
      ;;
  esac
  printf 'user %s on >%s %s\n' "$user" "$password" "$rest" >> "$out"
done < "$rules"
exec redis-server --aclfile "$out" "$@"
