# shellcheck shell=bash
# Helpers shared by the public-*.sh scripts (sourced, not executed).
#
# Conventions of the (private) hypervisor scripts: the scripts run on the workstation, reach the hypervisor over ssh and
# the CT through `pct exec`. A CT script is base64-encoded on this side and evaluated by bash
# in the CT, so no quoting layer of ssh or pct can alter it. With --dry-run (DRY_RUN=1) every
# remote action is printed and nothing is executed; local read-only git commands still run.
# No secret ever passes through here: only ids, hostnames and commit shas.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Machine settings (D-140): public.local.env, git-ignored, filled from public.env.example;
# PUBLIC_SETTINGS_FILE names another file. Variables already set in the environment win.
PUBLIC_SETTINGS_FILE="${PUBLIC_SETTINGS_FILE:-$here/public.local.env}"
if [ -f "$PUBLIC_SETTINGS_FILE" ]; then
  # shellcheck source=public.env.example
  . "$PUBLIC_SETTINGS_FILE"
fi
for setting in PUBLIC_SSH_HOST PUBLIC_CTID PUBLIC_SOURCE_CTID PUBLIC_DIR PUBLIC_ENV_FILE   PUBLIC_PROJECT PUBLIC_SOURCE_PROJECT PUBLIC_ZFS_POOL PUBLIC_ZFS_SOURCE PUBLIC_ZFS_PARENT   PUBLIC_BACKUP_HOST_DIR PUBLIC_SWITCH_STATE_DIR PUBLIC_PBS_STORAGE PUBLIC_NATIONAL_MEMORY_MB; do
  [ -n "${!setting:-}" ] || {
    printf '%s is not set: fill %s (template: %s/public.env.example)
'       "$setting" "$PUBLIC_SETTINGS_FILE" "$here" >&2
    exit 2
  }
done
unset setting
: "${PUBLIC_PROTECTED_CTIDS:=}"

DRY_RUN=0

log() { printf '%s\n' "$*" >&2; }

need_numeric() { # need_numeric <name> <value>
  case "$2" in ''|*[!0-9]*) log "$1 must be numeric, got '$2'"; exit 2 ;; esac
}

# dump_factor_ok <value>: a decimal number above 0 (a comma or 0 would void the space guard).
dump_factor_ok() {
  [[ "$1" =~ ^[0-9]+(\.[0-9]+)?$ ]] && awk -v f="$1" 'BEGIN { exit !(f > 0) }'
}

check_ids() {
  need_numeric PUBLIC_CTID "$PUBLIC_CTID"
  need_numeric PUBLIC_SOURCE_CTID "$PUBLIC_SOURCE_CTID"
  [ "$PUBLIC_CTID" != "$PUBLIC_SOURCE_CTID" ] || { log "target and source CT are the same"; exit 2; }
  local protected
  for protected in $PUBLIC_PROTECTED_CTIDS; do
    [ "$PUBLIC_CTID" != "$protected" ] || { log "ct/$protected is protected (PUBLIC_PROTECTED_CTIDS)"; exit 2; }
  done
}

# pve <command string>: one command on the hypervisor.
pve() {
  if [ "$DRY_RUN" = 1 ]; then printf '+ ssh %s %s\n' "$PUBLIC_SSH_HOST" "$*"; return 0; fi
  ssh -o BatchMode=yes "$PUBLIC_SSH_HOST" "$*" </dev/null
}

# local_run <command...>: a local command that writes (archive, scp).
local_run() {
  if [ "$DRY_RUN" = 1 ]; then printf '+ %s\n' "$*"; return 0; fi
  "$@"
}

# ct_payload <ctid> [NAME=value ...] < script: remote command line for one CT script.
# The values are ids and shas (printf %q quoted), never secrets.
ct_payload() {
  local ctid="$1" kv prelude="" body payload
  shift
  for kv in "$@"; do prelude+="$(printf 'export %s=%q\n' "${kv%%=*}" "${kv#*=}")"$'\n'; done
  body="set -euo pipefail"$'\n'"$prelude$(cat)"
  payload="$(printf '%s\n' "$body" | base64 -w0)"
  printf "pct exec %s -- bash -c 'eval \"\$(printf %%s %s | base64 -d)\"'" "$ctid" "$payload"
}

# host_script <read|write> [NAME=value ...] < script: run a bash script on the hypervisor itself
# (zfs, pct, files of the host). The script goes through the standard input of ssh and is
# read whole before it runs (`bash -c "$(cat)"`): no command line length limit, and no
# command of the script can read its remaining lines. A read script (surveys, refusals)
# also runs with --dry-run; a write script is then only printed.
host_script() {
  local kind="$1" script kv prelude="" body
  shift
  script="$(cat)"
  if [ "$DRY_RUN" = 1 ] && [ "$kind" = write ]; then
    printf '+ ssh %s bash  # host script:\n' "$PUBLIC_SSH_HOST"
    printf '%s\n' "$script" | sed 's/^/    | /'
    return 0
  fi
  for kv in "$@"; do prelude+="$(printf 'export %s=%q\n' "${kv%%=*}" "${kv#*=}")"$'\n'; done
  body="set -euo pipefail"$'\n'"$prelude$script"
  printf '%s\n' "$body" | ssh -o BatchMode=yes "$PUBLIC_SSH_HOST" 'bash -c "$(cat)" host-script'
}

# ct_script <ctid> [NAME=value ...] < script: run the script in CT <ctid>.
# Exit code = the script's. Transport failures (ssh 255, pct) are not told from it here:
# every script prints its own final marker line.
ct_script() {
  local ctid="$1" script
  script="$(cat)"
  if [ "$DRY_RUN" = 1 ]; then
    printf '+ ssh %s pct exec %s -- bash  # script:\n' "$PUBLIC_SSH_HOST" "$ctid"
    printf '%s\n' "$script" | sed 's/^/    | /'
    return 0
  fi
  local cmd
  cmd="$(printf '%s\n' "$script" | ct_payload "$@")"
  ssh -o BatchMode=yes "$PUBLIC_SSH_HOST" "$cmd" </dev/null
}
