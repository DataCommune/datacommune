# shellcheck shell=bash
# Helpers shared by deploy.sh, down.sh and psql.sh (sourced, not executed).
#
# Same conventions as infra/public/lib.sh, without hypervisor nor `pct`: the scripts run on the
# workstation and reach the rented server directly over ssh, as $DEPLOYMENT_USER (member of
# the docker group, owner of $PROD_DIR; created by provision.sh). A remote script is
# base64-encoded here and evaluated by bash on the server, so no ssh quoting layer can alter
# it and its stdin stays free (</dev/null). With --dry-run (DRY_RUN=1) every remote action
# is printed and nothing is executed; local read-only git commands still run.
# No secret ever passes through here: only hostnames, paths and commit shas.

lib_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Non-secret settings: infra/prod/prod.env (copied from prod.env.example), then defaults.
# Every value can be overridden from the environment of the caller.
PROD_SETTINGS_FILE="${PROD_SETTINGS_FILE:-$lib_here/prod.env}"
if [ -f "$PROD_SETTINGS_FILE" ]; then
  # shellcheck source=prod.env.example
  . "$PROD_SETTINGS_FILE"
fi
: "${DEPLOYMENT_HOST:=}"
: "${DEPLOYMENT_USER:=datacommune}"
: "${PROD_DIR:=/opt/datacommune}"
: "${PROD_ENV_FILE:=$PROD_DIR/.env}"
: "${PROD_PROJECT:=datacommune-prod}"
: "${PROD_DOMAIN:=prod.datacommune.fr}"
: "${PROD_API_DOMAIN:=api-prod.datacommune.fr}"
: "${APEX_DOMAIN:=datacommune.fr}"
: "${API_DOMAIN:=api.datacommune.fr}"
: "${ANON_KEY_QUOTA:=500}"

DRY_RUN=0

log() { printf '%s\n' "$*" >&2; }

# ssh_target: user@host, refused (2) when the host is unknown outside --dry-run.
ssh_target() {
  if [ -z "$DEPLOYMENT_HOST" ]; then
    if [ "$DRY_RUN" = 1 ]; then printf '%s@<DEPLOYMENT_HOST>' "$DEPLOYMENT_USER"; return 0; fi
    log "DEPLOYMENT_HOST is not set (infra/prod/prod.env, see prod.env.example)"
    exit 2
  fi
  case "$DEPLOYMENT_HOST$DEPLOYMENT_USER" in
    *[!A-Za-z0-9.:_-]*) log "DEPLOYMENT_HOST / DEPLOYMENT_USER contain unexpected characters"; exit 2 ;;
  esac
  printf '%s@%s' "$DEPLOYMENT_USER" "$DEPLOYMENT_HOST"
}

# local_run <command...>: a local command that writes or reaches the network (archive, scp).
local_run() {
  if [ "$DRY_RUN" = 1 ]; then printf '+ %s\n' "$*"; return 0; fi
  "$@"
}

# remote_script [NAME=value ...] < script: run the script on the server as $DEPLOYMENT_USER.
# The values are paths, names and shas (printf %q quoted), never secrets.
# Exit code = the script's; every script prints its own final marker line, so an ssh
# failure without that line is a transport failure.
remote_script() {
  local target kv prelude="" script body payload
  target="$(ssh_target)" || return 2
  script="$(cat)"
  for kv in "$@"; do prelude+="$(printf 'export %s=%q' "${kv%%=*}" "${kv#*=}")"$'\n'; done
  if [ "$DRY_RUN" = 1 ]; then
    printf '+ ssh %s bash  # script:\n' "$target"
    printf '%s%s\n' "$prelude" "$script" | sed 's/^/    | /'
    return 0
  fi
  body="set -euo pipefail"$'\n'"$prelude$script"
  payload="$(printf '%s\n' "$body" | base64 -w0)"
  ssh -o BatchMode=yes "$target" "bash -c 'eval \"\$(printf %s $payload | base64 -d)\"'" </dev/null
}
