#!/usr/bin/env bash
# Copy the database of the work base container into the public container.
#
#   infra/public/public-db-restore.sh [--dry-run] [--reset-db]
#   PG_DUMP_EXTRA='--exclude-table-data=<table>' infra/public/public-db-restore.sh
#   infra/public/public-db-restore.sh --from-zfs-clone [--deploy <commit>] [--apply]
#   infra/public/public-db-restore.sh --from-zfs-clone --rollback <stamp> [--apply]
#   infra/public/public-db-restore.sh --from-zfs-clone --drop-previous <stamp> [--apply]
#
# DUMP MODE (default, 44/85). Prerequisite: the public db container runs (public-deploy.sh
# --db-only). Run on the workstation. The source is only READ: pg_dump -Fc in a transaction
# that is read-only by construction (PGOPTIONS default_transaction_read_only=on), streamed on
# the hypervisor from the work base container into a file of the public container -- the data never goes through the workstation.
# Then, in the public container: `pg_restore --list` (integrity of the archive), database recreated from
# template0 (it only holds the extensions the postgis image creates), `pg_restore`
# --no-owner --no-privileges --exit-on-error, ANALYZE, publications re-anchored
# (public-reanchor.sql), revision printed.
# Idempotent: a target that already holds alembic_version is left alone (exit 0,
# "ALREADY_RESTORED"). --reset-db restores anyway: it DROPS the target database (and kills
# its connections) first -- never use it on a live public instance without a reason.
# The hosted scope is stored in the database (`python -m app.scope`), so a full dump
# carries it. Check what the work base holds that must not become public (README).
# Before any write: refused (80) when the rootfs of the public container has less free space than the source
# size x (PUBLIC_DUMP_SIZE_FACTOR + 1) + PUBLIC_DUMP_RESERVE_MB (the dump plus the restored base).
# Exit codes: 0 restored or already restored | 2 usage | 80 refused | 81 failed.
#
# --from-zfs-clone: NATIONAL PUBLICATION (lever L3 of the phase 4.2 hosting study): the public CT is
# switched onto a ZFS clone of the work pgdata instead of a dump. --dry-run is the default
# here: the read-only surveys and refusals run, every write is printed. --apply writes.
#   1. refused (80) while an import runs in the work base container (unit dc-national active, container
#      dcimport-* or dc-national-*), without a PBS backup of the public container younger than 24 h, without
#      the pool, or with the pool above 85 %, when the work pgdata is not the dataset,
#      when a CT is not unprivileged, when the work and public clusters differ in superuser
#      or database name, when the work cluster has a role outside its superuser and
#      WORK_ROLES, when the public container already mounts a clone not verified (or a foreign mount), when
#      the snapshot or the clone of the stamp exists, or when the backup directory has less
#      free space than the public db size x PUBLIC_DUMP_SIZE_FACTOR + PUBLIC_DUMP_RESERVE_MB
#      (the site still serves: a refusal stops nothing);
#   2. pg_dump -Fc of the public base (read-only transaction) to
#      $PUBLIC_BACKUP_HOST_DIR/public-pre-national-<stamp>.dump: non empty, header PGDMP;
#   3. the public api is stopped (the site is down from here), then the public application
#      rows (APP_TABLES) are exported to $PUBLIC_BACKUP_HOST_DIR/public-app-rows-<stamp>/
#      on the host: column list, row count and COPY text per table, count checked;
#   4. CHECKPOINT in the work db; zfs snapshot <source>@pub-<stamp>; the parent dataset
#      created if missing; zfs clone -o recordsize=32K into <parent>/pg-<stamp>. The
#      snapshot is consistent as after a power loss: PGDATA and pg_wal are in the same
#      dataset and PostgreSQL replays its WAL when it starts;
#   5. the state of before (mpN at the pgdata path or none, memory, served sha) is written to
#      $PUBLIC_SWITCH_STATE_DIR/switch-<stamp>; the containers of the public CT are stopped (api,
#      db, the rest); pct stop; pct set -mpN <clone>,mp=<pgdata volume> -memory 12288;
#      pct start; db only started and awaited healthy;
#   6. public state of the copy, in the public db, local socket: public-reset-temoin.sql
#      (DROP DATABASE datacommune_temoin); password of the superuser set from the
#      POSTGRES_PASSWORD of the db container (psql \getenv, never printed); the exported rows
#      staged and public-reset.sql played: work roles dropped, application tables emptied
#      and reloaded, compared row by row with the export; api_keys must equal the export;
#   7. public-reanchor.sql, always played and printed: STALE must be 0 (the clone keeps the
#      OIDs, so it is expected to move nothing);
#   8. public-deploy.sh --dry-run <commit>, then public-deploy.sh <commit>: it sees the clone
#      mounted and takes the snapshot <clone>@pre-<sha12>-<date> instead of a pre-deploy dump,
#      which would not fit the rootfs (04.2-05) (it starts the
#      api: the code served before never reads the schema of the clone); through the caddy
#      loopback of the public container with the anonymous key of its .env: /v1/health, a commune of 44
#      and three addresses (Paris, Lyon, Saint-Denis de La Reunion) in 200 with data. Then
#      $PUBLIC_SWITCH_STATE_DIR/verified-<stamp> and NATIONAL_SWITCH_OK.
#   Any failure of steps 5 to 8 rolls back automatically.
# --rollback <stamp>: containers stopped, pct stop, the mpN and memory of before restored
#   (the ext4 volume was left intact under the mount), pct start, mounts compared with the
#   state of before, then public-deploy.sh <served sha of before> and the checks; when the
#   pre-national dump of the stamp is on the host, it replaces the pre-deploy dump
#   (--backup-ref file:<dump>): it holds the base of before but the rows written between the
#   dump and the api stop (step 3), and that deploy migrates nothing.
# --drop-previous <stamp>: the ONLY path that deletes: zfs destroy of <parent>/pg-<stamp>,
#   after the snapshots @pre-* public-deploy.sh took of it, then of <source>@pub-<stamp>;
#   refused while that clone is mounted, or while the clone
#   mounted is not verified. Nothing else is ever destroyed (no rm -rf, no zfs destroy).
# Exit codes (--from-zfs-clone): 0 NATIONAL_SWITCH_OK, ROLLBACK_OK, DROP_PREVIOUS_OK or
#   dry-run printed | 2 usage | 80 refused, nothing changed | 81 failed before the switch:
#   public container on its own base, api started again; dump, export, snapshot and clone made so far
#   are kept and named | 82 failed after the switch, rolled back and verified on the base of
#   before | 83 rollback failed: the state is printed, rerun --rollback <stamp> --apply.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$here/lib.sh"

# Application tables of the public base: the only rows of the public CT that survive a
# switch, in reload order (parents first). saved_searches references users.
readonly APP_TABLES=(users api_keys subscriptions saved_searches quota_usage quota_usage_account admin_audit_log stripe_events chat_sessions chat_messages api_request_log signalements)
# Roles of the work cluster besides its superuser (the private workbase-reader-role.sh of the hypervisor).
readonly WORK_ROLES=(wb_reader)

reset=0 zfs_mode=0 apply=0 action=switch target="" deploy_rev=HEAD
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --reset-db) reset=1; shift ;;
    --ctid) PUBLIC_CTID="${2:-}"; shift 2 ;;
    --from-zfs-clone) zfs_mode=1; shift ;;
    --apply) apply=1; shift ;;
    --rollback) action=rollback; target="${2:-}"; shift 2 || { log "--rollback <stamp>"; exit 2; } ;;
    --drop-previous) action=drop; target="${2:-}"; shift 2 || { log "--drop-previous <stamp>"; exit 2; } ;;
    --deploy) deploy_rev="${2:-}"; shift 2 || { log "--deploy <commit>"; exit 2; } ;;
    -h|--help) sed -n '2,70p' "$0"; exit 0 ;;
    *) log "unknown argument: $1"; exit 2 ;;
  esac
done
check_ids

# ===== national publication by ZFS clone ===================================================
if [ "$zfs_mode" = 1 ]; then
  [ "$reset" = 0 ] || { log "--reset-db is not a --from-zfs-clone option"; exit 2; }
  [ "$apply" = 1 ] && [ "$DRY_RUN" = 1 ] && { log "--apply and --dry-run exclude each other"; exit 2; }
  [ "$apply" = 1 ] || DRY_RUN=1
  if [ "$action" != switch ]; then
    [[ "$target" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { log "stamp must look like 20261030T120000Z"; exit 2; }
  fi
  need_numeric PUBLIC_NATIONAL_MEMORY_MB "$PUBLIC_NATIONAL_MEMORY_MB"
  dump_factor_ok "${PUBLIC_DUMP_SIZE_FACTOR:-0.5}" ||
    { log "PUBLIC_DUMP_SIZE_FACTOR must be a number above 0 (0.5), got '${PUBLIC_DUMP_SIZE_FACTOR:-}'"; exit 2; }
  need_numeric PUBLIC_DUMP_RESERVE_MB "${PUBLIC_DUMP_RESERVE_MB:-2048}"
  # The rollback passes the pre-national dump to public-deploy.sh --backup-ref file:<path>.
  [[ "$PUBLIC_BACKUP_HOST_DIR" =~ ^/[A-Za-z0-9/._-]+$ ]] ||
    { log "PUBLIC_BACKUP_HOST_DIR must be an absolute path of [A-Za-z0-9/._-]"; exit 2; }
  DEPLOY="${PUBLIC_DEPLOY_SCRIPT:-$here/public-deploy.sh}"
  b64() { base64 -w0 < "$here/$1"; }

  # Functions shared by the scripts that run on the hypervisor (prepended to each).
  read -r -d '' HOST_LIB <<'LIB' || true
refuse() { echo "REFUSED: $*" >&2; exit 80; }
# psql in the db container of a compose project of a CT, local socket, superuser of the
# image; the values travel as positional parameters, never re-parsed by a shell.
IN_DB='if [ -n "$SQL" ]; then exec psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "${DB:-$POSTGRES_DB}" -c "$SQL"; else exec psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "${DB:-$POSTGRES_DB}"; fi'
CT_DB='c=$(docker ps -q --filter "label=com.docker.compose.project=$1" --filter label=com.docker.compose.service=db | head -1); [ -n "$c" ] || { echo "no running db container in project $1" >&2; exit 81; }; exec docker exec -i -e DB="$2" -e SQL="$3" "$c" sh -c "$4"'
db_psql() { # db_psql <ctid> <project> <database or ""> [<sql>]: SQL on stdin, or <sql> and COPY data on stdin
  pct exec "$1" -- sh -c "$CT_DB" ct-db "$2" "$3" "${4:-}" "$IN_DB"
}
IN_DUMP='exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
CT_DUMP='c=$(docker ps -q --filter "label=com.docker.compose.project=$1" --filter label=com.docker.compose.service=db | head -1); [ -n "$c" ] || exit 81; exec docker exec -e PGOPTIONS="-c default_transaction_read_only=on" "$c" sh -c "$2"'
db_dump() { pct exec "$1" -- sh -c "$CT_DUMP" ct-dump "$2" "$IN_DUMP"; }
ids_of() { # ids_of <ctid> <project> <service|""> [-a]: container ids
  local f=(--filter "label=com.docker.compose.project=$2")
  [ -z "$3" ] || f+=(--filter "label=com.docker.compose.service=$3")
  pct exec "$1" -- docker ps -q ${4:-} "${f[@]}"
}
svc() { # svc <ctid> <project> <stop|start> <service...>; stop checks nothing still runs
  local ctid="$1" project="$2" action="$3" s ids
  shift 3
  for s in "$@"; do
    ids="$(ids_of "$ctid" "$project" "$s" -a)" || return 1
    [ -n "$ids" ] || continue
    # shellcheck disable=SC2086
    pct exec "$ctid" -- docker "$action" $ids >/dev/null || return 1
    if [ "$action" = stop ] && [ -n "$(ids_of "$ctid" "$project" "$s")" ]; then
      echo "$s still running in CT $ctid" >&2; return 1
    fi
  done
}
stop_all() { # api, db, then every other container of the project
  local rest
  svc "$CTID" "$PROJECT" stop api db || return 1
  rest="$(ids_of "$CTID" "$PROJECT" "")" || return 1
  # shellcheck disable=SC2086
  [ -z "$rest" ] || pct exec "$CTID" -- docker stop $rest >/dev/null
}
wait_docker() { local i; for i in $(seq 1 60); do pct exec "$1" -- docker info >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }
wait_db() { # wait_db <ctid> <project>: db healthy within 300 s
  local i c s=""
  for i in $(seq 1 100); do
    c="$(ids_of "$1" "$2" db | head -1)" || c=""
    s=""; [ -z "$c" ] || s="$(pct exec "$1" -- docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || true)"
    [ "$s" = healthy ] && return 0
    sleep 3
  done
  echo "db of CT $1 not healthy (last state: ${s:-none})" >&2; return 1
}
ct_status() { pct status "$1" | tr -d '\r' | awk '{ print $2 }'; }
cfg_of() { pct config "$1" | tr -d '\r'; }
pg_mount() { # "<mpN> <value>" of the mount of CT $CTID at the pgdata volume, nothing if none
  cfg_of "$CTID" | awk -v t="mp=$PGDATA" '/^mp[0-9]+: / {
    n = split($0, f, ","); for (i = 2; i <= n; i++) if (f[i] == t) { k = $1; sub(/:$/, "", k); v = $0; sub(/^mp[0-9]+: /, "", v); print k, v } }'
}
mount_src() { local v="${1#* }"; printf '%s\n' "${v%%,*}"; }
free_mp() {
  local cfg i; cfg="$(cfg_of "$CTID")"
  for i in 0 1 2 3 4 5 6 7 8 9; do
    printf '%s\n' "$cfg" | awk -v k="mp$i:" 'index($0, k) == 1 { f = 1 } END { exit f }' && { echo "mp$i"; return 0; }
  done
  return 1
}
memory_of() { cfg_of "$CTID" | sed -n 's/^memory: *//p'; }
state() { sed -n "s/^$1=//p" "$STATE_DIR/switch-$2"; }
# checks <national 0|1>: through the caddy loopback of CT $CTID with the anonymous key of
# its .env (in a mode 600 header file, never printed).
IN_CT_CHECKS='k="$(grep -m1 "^PUBLIC_DC_API_KEY=" "$1" | cut -d= -f2-)"
case "$k" in dc_anon_?*) ;; *) echo "PUBLIC_DC_API_KEY missing or not dc_anon_" >&2; exit 1 ;; esac
h="$(mktemp)"; trap '"'"'rm -f "$h" "$h.b"'"'"' EXIT
( umask 077; printf "X-API-Key: %s\n" "$k" > "$h" ); unset k
bad=0
check() {
  code="$(curl -sS -m 20 -o "$h.b" -w "%{http_code}" -H "Host: api.datacommune.fr" -H "@$h" "http://127.0.0.1:8080$2")" || code=000
  ok=0; [ "$code" = 200 ] && ok=1
  if [ "$3" = data ] && grep -q "\"donnees\":\[\]" "$h.b"; then ok=0; fi
  if [ "$ok" = 1 ]; then echo "CHECK $1 $code"; else echo "CHECK $1 $code FAIL"; bad=1; fi
}
check health /v1/health -
check commune-44109 "/v1/commune?code_insee=44109" data
if [ "$2" = 1 ]; then
  check adresse-paris "/v1/geocode?q=rue%20de%20Rivoli%20Paris&code_postal=75001&limit=1" data
  check adresse-lyon "/v1/geocode?q=place%20Bellecour%20Lyon&code_postal=69002&limit=1" data
  check adresse-saint-denis-reunion "/v1/geocode?q=rue%20de%20Paris%20Saint-Denis&code_postal=97400&limit=1" data
fi
exit "$bad"'
checks() { pct exec "$CTID" -- sh -c "$IN_CT_CHECKS" ct-checks "$ENV_FILE" "$1"; }
LIB

  stamp="${target:-$(date -u +%Y%m%dT%H%M%SZ)}"
  roles_csv="$(IFS=,; echo "${WORK_ROLES[*]}")"
  HOST_VARS=(
    "CTID=$PUBLIC_CTID" "SRC=$PUBLIC_SOURCE_CTID" "PROJECT=$PUBLIC_PROJECT"
    "SRC_PROJECT=$PUBLIC_SOURCE_PROJECT"
    "PGDATA=/var/lib/docker/volumes/${PUBLIC_PROJECT}_pgdata/_data"
    "SRC_PGDATA=/var/lib/docker/volumes/${PUBLIC_SOURCE_PROJECT}_pgdata/_data"
    "POOL=$PUBLIC_ZFS_POOL" "ZSRC=$PUBLIC_ZFS_SOURCE" "ZPARENT=$PUBLIC_ZFS_PARENT"
    "BACKUP_DIR=$PUBLIC_BACKUP_HOST_DIR" "STATE_DIR=$PUBLIC_SWITCH_STATE_DIR"
    "PBS=$PUBLIC_PBS_STORAGE" "MEM=$PUBLIC_NATIONAL_MEMORY_MB" "STAMP=$stamp"
    "PUBLIC_DIR=$PUBLIC_DIR" "ENV_FILE=$PUBLIC_ENV_FILE"
    "TABLES=${APP_TABLES[*]}" "ROLES=$roles_csv"
    "DUMP_FACTOR=${PUBLIC_DUMP_SIZE_FACTOR:-0.5}" "DUMP_RESERVE_MB=${PUBLIC_DUMP_RESERVE_MB:-2048}"
    "TEMOIN_SQL_B64=$(b64 public-reset-temoin.sql)" "RESET_SQL_B64=$(b64 public-reset.sql)"
    "REANCHOR_SQL_B64=$(b64 public-reanchor.sql)"
  )
  # on_host <read|write> < script: HOST_LIB + script on the hypervisor with HOST_VARS.
  on_host() {
    local kind="$1" script
    script="$(cat)"
    if [ "$DRY_RUN" = 1 ] && [ "$kind" = write ]; then
      printf '%s\n' "$script" | host_script write
      return 0
    fi
    printf '%s\n%s\n' "$HOST_LIB" "$script" | host_script read "${HOST_VARS[@]}"
  }
  run_local() { # a local command that writes (public-deploy.sh); printed with --dry-run
    if [ "$DRY_RUN" = 1 ]; then printf '+ %s\n' "$*"; return 0; fi
    "$@"
  }

  # The CT is restored on the mount of before; then the release served before.
  rollback() { # rollback <stamp> <manual 0|1>; exits 0 (manual) / 82, or 83
    local s="$1" manual="$2" old rc=0
    old="$(STAMP_RB="$s" MANUAL="$manual" on_host_rb read <<'HOST'
f="$STATE_DIR/switch-$STAMP_RB"
[ -f "$f" ] || refuse "no switch state $f"
key="$(state KEY "$STAMP_RB")"
mnt="$(zfs get -H -o value mountpoint "$ZPARENT/pg-$STAMP_RB" 2>/dev/null || true)"
cur="$(pg_mount)"
if [ "$MANUAL" = 1 ] && { [ "${cur%% *}" != "$key" ] || [ "$(mount_src "$cur")" != "$mnt" ]; }; then
  refuse "$ZPARENT/pg-$STAMP_RB is not the pgdata mount ($key) of CT $CTID: nothing to roll back"
fi
echo "$(state OLD_SHA "$STAMP_RB")"
# The pre-national dump holds the base of before (the deploy of the rollback migrates nothing).
f="$BACKUP_DIR/public-pre-national-$STAMP_RB.dump"
if [ -s "$f" ] && [ "$(head -c 5 "$f")" = PGDMP ]; then echo "file:$f"; else echo ""; fi
HOST
)" || { [ "$manual" = 1 ] && exit 80; log "ROLLBACK_FAILED: switch state of $s unreadable"; exit 83; }
    local ref; ref="$(printf '%s\n' "$old" | sed -n 2p)"; old="$(printf '%s\n' "$old" | sed -n 1p)"
    local ref_args=(); [ -z "$ref" ] || ref_args=(--backup-ref "$ref")
    log "rollback of $s: mount of before restored, then release ${old}"
    STAMP_RB="$s" on_host_rb write <<'HOST' || rc=$?
key="$(state KEY "$STAMP_RB")"; prev="$(state PREVIOUS "$STAMP_RB")"; mem="$(state MEMORY "$STAMP_RB")"
mnt="$(zfs get -H -o value mountpoint "$ZPARENT/pg-$STAMP_RB" 2>/dev/null || true)"
cur="$(pg_mount)"
if [ -n "$cur" ] && [ "$(mount_src "$cur")" = "$mnt" ]; then
  if [ "$(ct_status "$CTID")" = running ]; then stop_all || true; pct stop "$CTID"; fi
  if [ "$prev" = none ]; then pct set "$CTID" -delete "$key"; else pct set "$CTID" "-$key" "$prev"; fi
fi
if [ -n "$mem" ]; then pct set "$CTID" -memory "$mem"; else pct set "$CTID" -delete memory; fi
[ "$(ct_status "$CTID")" = running ] || pct start "$CTID"
wait_docker "$CTID" || { echo "docker of CT $CTID does not answer" >&2; exit 1; }
now="$(pg_mount)"
if [ "$prev" = none ]; then [ -z "$now" ]; else [ "$now" = "$key $prev" ]; fi ||
  { echo "pgdata mount of CT $CTID is '${now:-none}', not the one of before" >&2; exit 1; }
[ "$(memory_of)" = "$mem" ] || { echo "memory of CT $CTID differs from before" >&2; exit 1; }
echo "MOUNT_RESTORED ${prev}" >&2
HOST
    [ "$rc" = 0 ] || { log "ROLLBACK_FAILED: CT $PUBLIC_CTID not back on its mount of before (stamp $s)"; exit 83; }
    if [ "$old" != none ]; then
      PUBLIC_CTID="$PUBLIC_CTID" run_local "$DEPLOY" "${ref_args[@]}" "$old" || rc=$?
    else
      on_host write <<'HOST' || rc=$?
ids="$(ids_of "$CTID" "$PROJECT" "" -a)"
# shellcheck disable=SC2086
[ -z "$ids" ] || pct exec "$CTID" -- docker start $ids >/dev/null
HOST
    fi
    [ "$rc" = 0 ] || { log "ROLLBACK_FAILED: the release of before does not come back (rc=$rc)"; exit 83; }
    if [ "$DRY_RUN" = 1 ]; then echo "ROLLBACK_DRY_RUN $s"; exit 0; fi
    on_host read <<'HOST' || { log "ROLLBACK_FAILED: checks of the base of before"; exit 83; }
checks 0
HOST
    if [ "$manual" = 1 ]; then echo "ROLLBACK_OK $s"; exit 0; fi
    echo "ROLLED_BACK $s: CT $PUBLIC_CTID serves the base of before; clone $PUBLIC_ZFS_PARENT/pg-$s kept"
    exit 82
  }
  on_host_rb() { # on_host with STAMP_RB / MANUAL added to the variables
    local saved=("${HOST_VARS[@]}")
    HOST_VARS+=("STAMP_RB=${STAMP_RB:-}" "MANUAL=${MANUAL:-0}")
    local rc=0
    on_host "$@" || rc=$?
    HOST_VARS=("${saved[@]}")
    return "$rc"
  }

  case "$action" in
    rollback) rollback "$target" 1 ;;
    drop)
      on_host read <<'HOST' || exit 80
zfs list -H -o name "$ZPARENT/pg-$STAMP" >/dev/null 2>&1 || refuse "$ZPARENT/pg-$STAMP does not exist"
mnt="$(zfs get -H -o value mountpoint "$ZPARENT/pg-$STAMP")"
cur="$(pg_mount)"; src="$(mount_src "$cur")"
[ "$src" != "$mnt" ] || refuse "$ZPARENT/pg-$STAMP is the pgdata of CT $CTID"
case "$src" in
  "/$ZPARENT"/pg-*) [ -f "$STATE_DIR/verified-${src##*/pg-}" ] ||
    refuse "the clone mounted in CT $CTID ($src) is not verified yet" ;;
esac
echo "drop: $ZPARENT/pg-$STAMP and $ZSRC@pub-$STAMP (CT $CTID on ${src:-its own volume})"
HOST
      on_host write <<'HOST' || { log "FAILED: destroying the clone or its snapshot"; exit 81; }
# The pre-deploy snapshots of the clone (public-deploy.sh), else zfs destroy refuses the clone.
for snap in $(zfs list -H -t snapshot -o name -d 1 "$ZPARENT/pg-$STAMP" | grep "^$ZPARENT/pg-$STAMP@pre-" || true); do
  zfs destroy "$snap"
done
zfs destroy "$ZPARENT/pg-$STAMP"
if zfs list -H -o name "$ZSRC@pub-$STAMP" >/dev/null 2>&1; then zfs destroy "$ZSRC@pub-$STAMP"; fi
install -d -m 0700 "$STATE_DIR"; date -u +%FT%TZ > "$STATE_DIR/dropped-$STAMP"
HOST
      [ "$DRY_RUN" = 1 ] && echo "DROP_PREVIOUS_DRY_RUN $stamp" || echo "DROP_PREVIOUS_OK $stamp"
      exit 0 ;;
  esac

  repo_root="$(git -C "$here" rev-parse --show-toplevel)"
  sha="$(git -C "$repo_root" rev-parse --verify --quiet "$deploy_rev^{commit}")" ||
    { log "unknown commit: $deploy_rev"; exit 2; }
  log "national switch, stamp $stamp, release $sha"

  # 1. Refusals (read-only, also with --dry-run).
  on_host read <<'HOST' || exit 80
[ "$(ct_status "$SRC")" = running ] || refuse "CT $SRC is not running"
[ "$(ct_status "$CTID")" = running ] || refuse "CT $CTID is not running"
if pct exec "$SRC" -- systemctl is-active --quiet dc-national; then
  refuse "the national import (unit dc-national) is active in CT $SRC"
fi
running="$(pct exec "$SRC" -- docker ps -q --filter name=dcimport- --filter name=dc-national-)" ||
  refuse "cannot list the containers of CT $SRC"
[ -z "$running" ] || refuse "an import container (dcimport-*, dc-national-*) runs in CT $SRC"
list="$(pvesm list "$PBS" --vmid "$CTID")" || refuse "cannot list the PBS backups of CT $CTID"
last="$(printf '%s\n' "$list" | tr -d '\r' | sed -n "s#.*backup/ct/$CTID/\([0-9][0-9T:Z-]*\).*#\1#p" | sort | tail -n 1)"
[ -n "$last" ] || refuse "no PBS backup of CT $CTID in $PBS"
then_s="$(date -u -d "$last" +%s 2>/dev/null)" || refuse "unreadable backup date: $last"
[ $(( $(date -u +%s) - then_s )) -le 86400 ] || refuse "last PBS backup of CT $CTID is older than 24 h ($last)"
cap="$(zpool list -H -o capacity "$POOL" 2>/dev/null | tr -d '%\r ')" || refuse "pool $POOL absent"
case "$cap" in ''|*[!0-9]*) refuse "pool $POOL absent or capacity unreadable" ;; esac
[ "$cap" -le 85 ] || refuse "pool $POOL at $cap % (> 85 %)"
zfs list -H -o name "$ZSRC" >/dev/null 2>&1 || refuse "dataset $ZSRC absent"
cfg_of "$SRC" | awk -v s="/$ZSRC" -v t="mp=$SRC_PGDATA" '/^mp[0-9]+: / {
  n = split($0, f, ","); src = f[1]; sub(/^mp[0-9]+: /, "", src)
  for (i = 2; i <= n; i++) if (f[i] == t && src == s) ok = 1 } END { exit !ok }' ||
  refuse "the pgdata of CT $SRC is not the dataset $ZSRC"
for c in "$SRC" "$CTID"; do
  cfg_of "$c" | awk '$0 == "unprivileged: 1" { f = 1 } END { exit !f }' ||
    refuse "CT $c is not unprivileged: the owners of the clone would not map"
done
who="$(db_psql "$SRC" "$SRC_PROJECT" "" "SELECT current_user || ' ' || current_database()")" ||
  refuse "cannot query the work db of CT $SRC"
pub="$(db_psql "$CTID" "$PROJECT" "" "SELECT current_user || ' ' || current_database()")" ||
  refuse "cannot query the public db of CT $CTID"
[ "$who" = "$pub" ] || refuse "work ($who) and public ($pub) differ in superuser or database name"
roles="$(db_psql "$SRC" "$SRC_PROJECT" "" "SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg\_%' ORDER BY 1")" ||
  refuse "cannot list the roles of the work cluster"
for r in $roles; do
  case ",${who%% *},$ROLES," in *",$r,"*) ;; *) refuse "role $r of the work cluster is neither its superuser nor one of $ROLES" ;; esac
done
cur="$(pg_mount)"
if [ -n "$cur" ]; then
  src="$(mount_src "$cur")"
  case "$src" in
    "/$ZPARENT"/pg-*) [ -f "$STATE_DIR/verified-${src##*/pg-}" ] ||
      refuse "clone ${src##*/} is mounted in CT $CTID and not verified: finish it, or --rollback ${src##*/pg-}" ;;
    *) refuse "CT $CTID mounts $src at $PGDATA, not a clone of this script" ;;
  esac
else
  free_mp >/dev/null || refuse "no free mpN index in CT $CTID"
fi
if zfs list -H -o name "$ZPARENT/pg-$STAMP" >/dev/null 2>&1; then refuse "$ZPARENT/pg-$STAMP exists"; fi
if zfs list -H -o name "$ZSRC@pub-$STAMP" >/dev/null 2>&1; then refuse "$ZSRC@pub-$STAMP exists"; fi
mountpoint -q "$(dirname "$BACKUP_DIR")" || refuse "$(dirname "$BACKUP_DIR") is not mounted"
# Space of the pre-national dump, checked while the site still serves.
psize="$(db_psql "$CTID" "$PROJECT" "" "SELECT pg_database_size(current_database())" | tr -d '\r ')" ||
  refuse "cannot read the size of the public db"
case "$psize" in ''|*[!0-9]*) refuse "size of the public db unreadable" ;; esac
need="$(awk -v s="$psize" -v f="$DUMP_FACTOR" -v r="$DUMP_RESERVE_MB" 'BEGIN { printf "%.0f", s * f + r * 1048576 }')"
bfree="$(df -B1 --output=avail "$(dirname "$BACKUP_DIR")" | tail -n 1 | tr -d ' ')"
case "$bfree" in ''|*[!0-9]*) refuse "free space of $(dirname "$BACKUP_DIR") unreadable" ;; esac
[ "$bfree" -ge "$need" ] ||
  refuse "pre-national dump needs ~$need bytes (public db $psize x $DUMP_FACTOR + ${DUMP_RESERVE_MB} MB), $bfree free in $(dirname "$BACKUP_DIR")"
echo "GUARDS_OK backup=$last pool=$cap% pgdata_mount=${cur:-none} dump_space=$bfree/$need" >&2
HOST

  # 2. Backup of the public base before anything changes.
  on_host write <<'HOST' || { log "FAILED: pre-national dump; nothing changed"; exit 81; }
install -d -m 0700 "$BACKUP_DIR"
f="$BACKUP_DIR/public-pre-national-$STAMP.dump"
( umask 077; db_dump "$CTID" "$PROJECT" > "$f.part" ) || { echo "pg_dump failed" >&2; exit 1; }
[ -s "$f.part" ] || { echo "empty dump $f.part" >&2; exit 1; }
[ "$(head -c 5 "$f.part")" = PGDMP ] || { echo "no PGDMP header in $f.part" >&2; exit 1; }
mv "$f.part" "$f"
echo "pre-national dump: $f ($(wc -c < "$f" | tr -d ' ') bytes)" >&2
HOST

  # 3. Public api stopped (site down from here), application rows exported to the host;
  # 4. snapshot and clone. A failure starts the api again.
  on_host write <<'HOST' || { log "FAILED before the switch: CT $PUBLIC_CTID on its own base, api started again (stamp $stamp)"; exit 81; }
back() { svc "$CTID" "$PROJECT" start api || echo "api of CT $CTID did not start again" >&2; exit 1; }
svc "$CTID" "$PROJECT" stop api || back
D="$BACKUP_DIR/public-app-rows-$STAMP"
install -d -m 0700 "$D" || back
for t in $TABLES; do
  ( umask 077
    db_psql "$CTID" "$PROJECT" "" "SELECT string_agg(quote_ident(attname), ',' ORDER BY attnum) FROM pg_attribute WHERE attrelid = 'public.$t'::regclass AND attnum > 0 AND NOT attisdropped AND attgenerated = ''" > "$D/$t.cols" &&
    db_psql "$CTID" "$PROJECT" "" "SELECT count(*) FROM public.$t" > "$D/$t.count" &&
    db_psql "$CTID" "$PROJECT" "" "COPY public.$t ($(cat "$D/$t.cols")) TO STDOUT" > "$D/$t.copy" ) || back
  [ -s "$D/$t.cols" ] || back
  [ "$(wc -l < "$D/$t.copy" | tr -d ' ')" = "$(cat "$D/$t.count")" ] || { echo "$t: exported lines differ from its count" >&2; back; }
  echo "exported $t: $(cat "$D/$t.count") rows" >&2
done
[ "$(cat "$D/api_keys.count")" -ge 1 ] || { echo "no api key in the public base" >&2; back; }
db_psql "$SRC" "$SRC_PROJECT" "" "CHECKPOINT" >/dev/null || back
zfs snapshot "$ZSRC@pub-$STAMP" || back
zfs list -H -o name "$ZPARENT" >/dev/null 2>&1 || zfs create -o recordsize=32K "$ZPARENT" || back
zfs clone -o recordsize=32K "$ZSRC@pub-$STAMP" "$ZPARENT/pg-$STAMP" || back
[ "$(zfs get -H -o value mounted "$ZPARENT/pg-$STAMP")" = yes ] || back
echo "clone $ZPARENT/pg-$STAMP of $ZSRC@pub-$STAMP" >&2
HOST

  # 5-7. Switch, public state of the copy, publication guard. Any failure rolls back.
  on_host write <<'HOST' || rollback "$stamp" 0
cur="$(pg_mount)"
if [ -n "$cur" ]; then key="${cur%% *}"; prev="${cur#* }"; else key="$(free_mp)"; prev=none; fi
old="$(pct exec "$CTID" -- cat "$PUBLIC_DIR/current_sha" 2>/dev/null | tr -d '\r ')" || old=""
install -d -m 0700 "$STATE_DIR"
printf 'KEY=%s\nPREVIOUS=%s\nMEMORY=%s\nOLD_SHA=%s\n' "$key" "$prev" "$(memory_of)" "${old:-none}" > "$STATE_DIR/switch-$STAMP"
stop_all
pct stop "$CTID"
mnt="$(zfs get -H -o value mountpoint "$ZPARENT/pg-$STAMP")"
pct set "$CTID" "-$key" "$mnt,mp=$PGDATA" -memory "$MEM"
pct start "$CTID"
wait_docker "$CTID"
svc "$CTID" "$PROJECT" start db
wait_db "$CTID" "$PROJECT"
echo "switched: $key=$mnt, memory $MEM" >&2

# 6. Public state of the copy.
out="$(printf '%s' "$TEMOIN_SQL_B64" | base64 -d | db_psql "$CTID" "$PROJECT" postgres)"
case "$out" in *TEMOIN_DROPPED*) ;; *) echo "witness database not dropped" >&2; exit 1 ;; esac
printf '%s\n' '\getenv u POSTGRES_USER' '\getenv pw POSTGRES_PASSWORD' \
  'SET pg_stat_statements.track_utility = off;' "ALTER ROLE :\"u\" PASSWORD :'pw';" |
  db_psql "$CTID" "$PROJECT" "" >/dev/null
echo "password of the superuser set from the environment of the public db container" >&2
D="$BACKUP_DIR/public-app-rows-$STAMP"
db_psql "$CTID" "$PROJECT" "" "DROP SCHEMA IF EXISTS public_reset_rows CASCADE; CREATE SCHEMA public_reset_rows" >/dev/null
counts=""
for t in $TABLES; do
  cols="$(cat "$D/$t.cols")"
  db_psql "$CTID" "$PROJECT" "" "CREATE TABLE public_reset_rows.$t AS SELECT $cols FROM public.$t WITH NO DATA" >/dev/null
  db_psql "$CTID" "$PROJECT" "" "COPY public_reset_rows.$t ($cols) FROM STDIN" < "$D/$t.copy" >/dev/null
  counts="$counts${counts:+,}$t=$(cat "$D/$t.count")"
done
out="$( { printf "SET public_reset.tables = '%s';\n" "$(printf '%s' "$TABLES" | tr ' ' ',')"
          printf "SET public_reset.counts = '%s';\n" "$counts"
          printf "SET public_reset.drop_roles = '%s';\n" "$ROLES"
          printf '%s' "$RESET_SQL_B64" | base64 -d; } | db_psql "$CTID" "$PROJECT" "")"
printf '%s\n' "$out" >&2
printf '%s\n' "$out" | awk -v w="PUBLIC_RESET_OK api_keys=$(cat "$D/api_keys.count")" '$0 == w { f = 1 } END { exit !f }' ||
  { echo "api_keys of the copy differ from the export" >&2; exit 1; }

# 7. Publication guard: re-anchor always played and printed.
out="$(printf '%s' "$REANCHOR_SQL_B64" | base64 -d | db_psql "$CTID" "$PROJECT" "")"
printf '%s\n' "$out" | sed 's/^/publication guard: /' >&2
printf '%s\n' "$out" | awk '/^PUBLISHED [0-9]+ NOT_PUBLISHED [0-9]+ STALE 0$/ { f = 1 } END { exit !f }' ||
  { echo "published relations not anchored on the clone" >&2; exit 1; }
HOST

  # 8. Release, then the checks of the national base. public-deploy.sh snapshots the clone
  # instead of dumping it (144 GB, 04.2-05).
  PUBLIC_CTID="$PUBLIC_CTID" run_local "$DEPLOY" --dry-run "$sha" || rollback "$stamp" 0
  PUBLIC_CTID="$PUBLIC_CTID" run_local "$DEPLOY" "$sha" || rollback "$stamp" 0
  if [ "$DRY_RUN" = 1 ]; then
    printf '+ checks through the caddy loopback of CT %s (health, commune 44109, Paris, Lyon, Saint-Denis 97400)\n' "$PUBLIC_CTID"
    echo "NATIONAL_SWITCH_DRY_RUN $stamp"
    exit 0
  fi
  on_host read <<'HOST' || rollback "$stamp" 0
checks 1
install -d -m 0700 "$STATE_DIR"; date -u +%FT%TZ > "$STATE_DIR/verified-$STAMP"
HOST
  echo "NATIONAL_SWITCH_OK $stamp: CT $PUBLIC_CTID serves $PUBLIC_ZFS_PARENT/pg-$stamp, release $sha"
  exit 0
fi

# ===== dump mode (44/85) ===================================================================
[ "$apply" = 0 ] && [ "$action" = switch ] && [ "$deploy_rev" = HEAD ] ||
  { log "--apply, --rollback, --drop-previous and --deploy need --from-zfs-clone"; exit 2; }
extra="${PG_DUMP_EXTRA:-}"
case "$extra" in *[\;\&\|\`\$\(\)\'\"\\]*) log "PG_DUMP_EXTRA: plain pg_dump options only"; exit 2 ;; esac

dump_file="$PUBLIC_DIR/dumps/work-base.pgdump"
vars=("PROJECT=$PUBLIC_PROJECT" "DUMP=$dump_file")

# 1. State of the target (db must run).
state="$(ct_script "$PUBLIC_CTID" "${vars[@]}" <<'IN_CT'
c="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=db | head -1)"
[ -n "$c" ] || { echo "REFUSED: no db container in project $PROJECT (run public-deploy.sh --db-only)" >&2; exit 80; }
r="$(docker exec -i "$c" sh -c 'psql -X -q -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT to_regclass('"'"'public.alembic_version'"'"') IS NOT NULL"' </dev/null 2>/dev/null || echo f)"
[ "$r" = t ] && echo "RESTORE_STATE=restored" || echo "RESTORE_STATE=empty"
IN_CT
)" || { log "$state"; exit 80; }
[ "$DRY_RUN" = 1 ] && printf '%s\n' "$state"
if [ "$state" = "RESTORE_STATE=restored" ] && [ "$reset" = 0 ]; then
  echo "ALREADY_RESTORED: target database holds alembic_version; --reset-db to redo"
  exit 0
fi

# 1b. Space guard, before anything is written: the dump and the restored base land on the
# rootfs of the public container (a national base of 144 GB does not fit it, 04.2-05).
dump_factor_ok "${PUBLIC_DUMP_SIZE_FACTOR:-0.5}" ||
  { log "PUBLIC_DUMP_SIZE_FACTOR must be a number above 0 (0.5), got '${PUBLIC_DUMP_SIZE_FACTOR:-}'"; exit 2; }
need_numeric PUBLIC_DUMP_RESERVE_MB "${PUBLIC_DUMP_RESERVE_MB:-2048}"
src_size="$(ct_script "$PUBLIC_SOURCE_CTID" "PROJECT=$PUBLIC_SOURCE_PROJECT" <<'IN_CT'
c="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=db | head -1)"
[ -n "$c" ] || { echo "no source db container in project $PROJECT" >&2; exit 80; }
echo "SELECT pg_database_size(current_database())" |
  docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' "$c" sh -c 'psql -X -q -At -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | tr -d '\r '
IN_CT
)" || { log "REFUSED: size of the source base unreadable"; exit 80; }
free="$(ct_script "$PUBLIC_CTID" "DIR=$PUBLIC_DIR" <<'IN_CT'
df -B1 --output=avail "$DIR" | tail -n 1 | tr -d ' '
IN_CT
)" || { log "REFUSED: free space of $PUBLIC_DIR in CT $PUBLIC_CTID unreadable"; exit 80; }
if [ "$DRY_RUN" != 1 ]; then
  for v in "$src_size" "$free"; do
    case "$v" in ''|*[!0-9]*) log "REFUSED: size of the source base or free space unreadable"; exit 80 ;; esac
  done
  # The dump, plus the restored base (about the size of the source).
  need="$(awk -v s="$src_size" -v f="${PUBLIC_DUMP_SIZE_FACTOR:-0.5}" -v r="${PUBLIC_DUMP_RESERVE_MB:-2048}" \
    'BEGIN { printf "%.0f", s * (f + 1) + r * 1048576 }')"
  [ "$free" -ge "$need" ] || {
    log "REFUSED: dump mode needs ~$need bytes in CT $PUBLIC_CTID (source $src_size x (${PUBLIC_DUMP_SIZE_FACTOR:-0.5} + 1) + ${PUBLIC_DUMP_RESERVE_MB:-2048} MB), $free free; the national base goes by --from-zfs-clone"
    exit 80
  }
fi

# 2. Dump (read-only on the source) streamed work base -> public container, on the hypervisor.
send="$(ct_payload "$PUBLIC_SOURCE_CTID" "PROJECT=$PUBLIC_SOURCE_PROJECT" "EXTRA=$extra" <<'IN_CT'
c="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=db | head -1)"
[ -n "$c" ] || { echo "no source db container in project $PROJECT" >&2; exit 81; }
exec docker exec -e PGOPTIONS='-c default_transaction_read_only=on' -e EXTRA="$EXTRA" "$c" \
  sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB" $EXTRA'
IN_CT
)"
recv="$(ct_payload "$PUBLIC_CTID" "DUMP=$dump_file" <<'IN_CT'
umask 077
install -d -m 0700 "$(dirname "$DUMP")"
cat > "$DUMP.part"
mv "$DUMP.part" "$DUMP"
ls -l "$DUMP" >&2
IN_CT
)"
pipeline="$send | $recv"
if [ "$DRY_RUN" = 1 ]; then
  printf '+ ssh %s bash -o pipefail -c  # pipeline:\n' "$PUBLIC_SSH_HOST"
  printf '    | %s\n' "$pipeline" | cut -c1-200
else
  payload="$(printf '%s\n' "$pipeline" | base64 -w0)"
  ssh -o BatchMode=yes "$PUBLIC_SSH_HOST" \
    "bash -o pipefail -c \"\$(printf %s $payload | base64 -d)\"" </dev/null ||
    { log "FAILED: dump transfer (source untouched; partial file removed on retry)"; exit 81; }
fi

# 3. Restore in the target, then re-anchor the publications (public-reanchor.sql).
rc=0
ct_script "$PUBLIC_CTID" "${vars[@]}" "REANCHOR_SQL_B64=$(base64 -w0 < "$here/public-reanchor.sql")" <<'IN_CT' || rc=$?
c="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=db | head -1)"
[ -n "$c" ] || { echo "no db container" >&2; exit 81; }
docker exec -i "$c" pg_restore --list < "$DUMP" > /dev/null || { echo "FAILED: archive unreadable" >&2; exit 81; }
docker exec "$c" sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" \
  -c "CREATE DATABASE \"$POSTGRES_DB\" TEMPLATE template0"'
docker exec -i "$c" sh -c 'pg_restore --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$DUMP"
docker exec "$c" sh -c 'vacuumdb --analyze-only -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
# A restore recreates every table under a new OID: the publication guard of the API would
# refuse every published source (public-reanchor.sql).
out="$(printf '%s' "$REANCHOR_SQL_B64" | base64 -d |
  docker exec -i "$c" sh -c 'psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"')"
printf '%s\n' "$out" >&2
printf '%s\n' "$out" | awk '/^PUBLISHED [0-9]+ NOT_PUBLISHED [0-9]+ STALE 0$/ { f = 1 } END { exit !f }' ||
  { echo "FAILED: published relation(s) still not anchored" >&2; exit 81; }
rev="$(docker exec -i "$c" sh -c 'psql -X -q -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version_num FROM alembic_version"')"
rm -f "$DUMP"
echo "RESTORED alembic=$rev"
IN_CT
if [ "$rc" -ne 0 ]; then log "FAILED: restore (rc=$rc); rerun, or --reset-db"; exit 81; fi
