#!/usr/bin/env bash
# Stop, or tear down, the public stack in the CT. Nothing else on the hypervisor is touched.
#
#   infra/public/public-down.sh [--dry-run]                    # stop: containers kept, site offline
#   infra/public/public-down.sh [--dry-run] --remove           # containers and network removed, data kept
#   infra/public/public-down.sh [--dry-run] --purge-data       # ALSO deletes the volumes (database!)
#
# `stop` is reversible with `docker compose ... start` or public-deploy.sh (same commit,
# no rebuild needed). --purge-data destroys the public database and the caddy state: the
# database can be rebuilt from the work base (public-db-restore.sh) but not the pre-deploy
# dumps if $PUBLIC_DIR is removed with the CT. Destroying the CT itself stays a manual
# gesture on the hypervisor (README).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$here/lib.sh"

action=stop
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --remove) action=remove; shift ;;
    --purge-data) action=purge; shift ;;
    --ctid) PUBLIC_CTID="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) log "unknown argument: $1"; exit 2 ;;
  esac
done
check_ids

ct_script "$PUBLIC_CTID" "ACTION=$action" "PROJECT=$PUBLIC_PROJECT" <<'IN_CT'
ids="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")"
if [ -z "$ids" ]; then echo "NOTHING: no container in project $PROJECT"; exit 0; fi
case "$ACTION" in
  stop)   docker compose -p "$PROJECT" stop ;;
  remove) docker compose -p "$PROJECT" down --remove-orphans ;;
  purge)  docker compose -p "$PROJECT" down --remove-orphans --volumes ;;
esac
echo "DOWN action=$ACTION"
IN_CT
