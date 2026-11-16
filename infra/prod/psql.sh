#!/usr/bin/env bash
# Run one SQL statement in the production base, from the workstation.
#
#   infra/prod/psql.sh [--dry-run] "<sql>"
#
# On the server, from the release in $PROD_DIR/current_sha:
#   docker compose -p datacommune-prod ... exec -T db psql -U <POSTGRES_USER> -d <POSTGRES_DB> -c "<sql>"
# The user and the base are read by psql inside the db container from its own environment;
# no value of $PROD_ENV_FILE is read or printed here. The SQL travels as a plain value: never
# put a secret in it. Output: psql's (unaligned, tuples only).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$here/lib.sh"

sql=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    -*) log "unknown argument: $1"; exit 2 ;;
    *) [ -z "$sql" ] || { log "one SQL argument only"; exit 2; }; sql="$1"; shift ;;
  esac
done
[ -n "$sql" ] || { log "usage: $0 [--dry-run] \"<sql>\""; exit 2; }

remote_script "DIR=$PROD_DIR" "ENV_FILE=$PROD_ENV_FILE" "PROJECT=$PROD_PROJECT" "SQL=$sql" <<'IN_SERVER'
[ -f "$DIR/current_sha" ] || { echo "REFUSED: nothing deployed yet ($DIR/current_sha missing)" >&2; exit 80; }
SHA="$(cat "$DIR/current_sha")"; TAG="${SHA:0:12}"
cd "$DIR/releases/$TAG"
IMAGE_TAG="$TAG" DEPLOYMENT_SHA="$SHA" docker compose -p "$PROJECT" --env-file "$ENV_FILE" \
  -f api/docker-compose.public.yml -f api/docker-compose.prod.yml \
  exec -T -e SQL="$SQL" db sh -c 'psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$SQL"'
IN_SERVER
