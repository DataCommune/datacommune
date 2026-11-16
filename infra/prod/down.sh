#!/usr/bin/env bash
# Stop the production stack on the rented server, volumes kept.
#
#   infra/prod/down.sh [--dry-run]
#
# `docker compose -p datacommune-prod down --remove-orphans`: containers and network removed,
# volumes (the base, caddy state) KEPT; this script never deletes a volume. The site is
# offline until infra/prod/deploy.sh runs again (same commit: the images are still there,
# `deploy.sh --rollback` or a deploy of current_sha restarts it). To cut the public access
# without touching the server: disable the routes of the tunnel in Cloudflare.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$here/lib.sh"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) log "unknown argument: $1"; exit 2 ;;
  esac
done

remote_script "PROJECT=$PROD_PROJECT" <<'IN_SERVER'
ids="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")"
if [ -z "$ids" ]; then echo "NOTHING: no container in project $PROJECT"; exit 0; fi
docker compose -p "$PROJECT" down --remove-orphans
echo "DOWN project=$PROJECT volumes=kept"
IN_SERVER
