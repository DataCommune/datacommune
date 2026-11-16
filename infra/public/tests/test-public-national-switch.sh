#!/usr/bin/env bash
# Prove public-db-restore.sh --from-zfs-clone (national publication on the public CT by ZFS clone)
# without the hypervisor, without ssh and without network.
#
# The script is copied with lib.sh, tests/public.test.env (as public.local.env) and the SQL files
# into a scratch git
# repository (one commit, the release to deploy). Doubles come first in PATH:
#   ssh       runs the remote command in a local bash, stdin passed through;
#   pct       status/config/set/stop/start on files of $SIM/ct/<id>; `pct exec <id> -- cmd`
#             runs cmd here with FAKE_CT=<id>;
#   docker    containers <ct>-<service> (running/stopped files), and psql/pg_dump in the db
#             container answered from tables of $SIM/db/<volume>/<table>.rows; the volume of
#             the public CT is the one its pct config mounts at the pgdata path (ext4 when none);
#             public-reset.sql is answered by moving the staged rows into the volume, so
#             the test sees which rows the script carried (the SQL itself is proven on a
#             real PostgreSQL by api/backend/tests/integration/test_public_national_reset.py);
#   zfs, zpool, pvesm, systemctl, curl, install, sleep, seq; public-deploy.sh is replaced
#             by a double (PUBLIC_DEPLOY_SCRIPT) that logs its arguments.
# Scenarios (exit codes of the header of public-db-restore.sh):
#   (a) nominal --apply -> 0 NATIONAL_SWITCH_OK; steps in order; public CT on the clone with
#       12288 MiB; the clone holds the public api key, logs and quotas, no work key; the
#       ext4 base untouched; reset and re-anchor played; deploy dry-run then real
#       (public-deploy.sh snapshots the clone itself, test-public-deploy.sh); checks
#   (b) default (dry-run) -> 0, surveys only: no write of any kind
#   (c) refusals -> 80, no write: dc-national active, import container, no PBS backup,
#       PBS backup older than 24 h, pool absent, pool at 86 %, unknown work role, clone
#       mounted and not verified, backup directory too small for the pre-national dump,
#       USB of the backups not mounted; usage (2): factor of the space guard 0,5 or 0
#   (d) dump without PGDMP header -> 81, api never stopped
#   (e) reset fails after the switch -> 82, public CT back on the same mounts and memory
#   (f) stale publication -> 82 (rolled back)
#   (g) --rollback <stamp> after (a) -> 0, pct config identical to the one of before,
#       release of before redeployed with --backup-ref file:<pre-national dump>, clone and
#       snapshot kept; without that dump (i), redeployed without --backup-ref
#   (h) --drop-previous: refused (80) while mounted; after the rollback -> 0, destroys
#       the @pre-* snapshots of the clone, the clone, then the snapshot, and nothing else
#   (i) second switch over a verified clone, then its rollback -> back on the first clone
#   (k) dump mode (44/85) of a 144 GB source with 18 GB free in the public CT -> 80 before any
#       dump or write
#   (j) never: zfs destroy outside --drop-previous, rm -rf in the script, COPY or dump of
#       the work CT, the api key on a command line
# Prints PUBLIC_SWITCH_TESTS_OK only if every scenario passes; otherwise names the failing
# scenario(s) and exits 1. PUBLIC_RESTORE_SCRIPT overrides the script under test (mutation).
# SWITCH_GROUP=chaine|refus|replis plays one group of scenarios (KI-19); the last but one line names
# the scenarios played (SCENARIOS a g h i).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
UNDER_TEST="${PUBLIC_RESTORE_SCRIPT:-$REPO/infra/public/public-db-restore.sh}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export SIM="$WORK/sim" FAKE_LOG="$WORK/calls.log"
FAKE_BIN="$WORK/bin"
GIT_REPO="$WORK/repo"
mkdir -p "$FAKE_BIN" "$GIT_REPO/infra/public" "$GIT_REPO/api"
cp "$UNDER_TEST" "$GIT_REPO/infra/public/public-db-restore.sh"
cp "$REPO"/infra/public/{lib.sh,public-reset.sql,public-reset-temoin.sql,public-reanchor.sql} \
  "$GIT_REPO/infra/public/"
cp "$REPO/infra/public/tests/public.test.env" "$GIT_REPO/infra/public/public.local.env"
SCRIPT="$GIT_REPO/infra/public/public-db-restore.sh"
git -C "$GIT_REPO" init -q
git -C "$GIT_REPO" config core.autocrlf false
echo release > "$GIT_REPO/api/release"
git -C "$GIT_REPO" add . >/dev/null
git -C "$GIT_REPO" -c user.name=t -c user.email=t@t commit -q -m release
SHA="$(git -C "$GIT_REPO" rev-parse HEAD)"
OLD_SHA=0123456789abcdef0123456789abcdef01234567
PGDATA=/var/lib/docker/volumes/datacommune-public_pgdata/_data
SECRET_KEY=dc_anon_doublesecretvalue

# --- doubles ---------------------------------------------------------------
cat > "$FAKE_BIN/ssh" <<'EOF'
#!/usr/bin/env bash
while [ "$#" -gt 0 ]; do case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
shift  # host
exec bash -c "$1"
EOF
cat > "$FAKE_BIN/pct" <<'EOF'
#!/usr/bin/env bash
d="$SIM/ct/$2"
case "$1" in
  status) read -r st < "$d/status"; echo "status: $st" ;;
  config) cat "$d/config" ;;
  exec)
    read -r st < "$d/status"; [ "$st" = running ] || exit 1
    id="$2"; shift 3; FAKE_CT="$id" exec "$@" ;;
  stop) echo "pct stop $2" >> "$FAKE_LOG"; echo stopped > "$d/status"
        for s in "$d"/svc/*; do echo stopped > "$s"; done ;;
  start) echo "pct start $2" >> "$FAKE_LOG"; echo running > "$d/status" ;;
  set)
    echo "pct $*" >> "$FAKE_LOG"
    shift 2
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -delete) k="$2"; awk -v k="$k:" 'index($0, k) != 1' "$d/config" > "$d/c" && mv "$d/c" "$d/config" ;;
        -*) k="${1#-}"; v="$2"
            if awk -v k="$k:" 'index($0, k) == 1 { f = 1 } END { exit !f }' "$d/config"; then
              awk -v k="$k:" -v v="$k: $v" 'index($0, k) == 1 { print v; next } { print }' "$d/config" > "$d/c" && mv "$d/c" "$d/config"
            else echo "$k: $v" >> "$d/config"; fi ;;
      esac
      shift 2
    done ;;
  *) exit 1 ;;
esac
EOF
cat > "$FAKE_BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $* (ct ${FAKE_CT:-host})" >> "$FAKE_LOG"
[ "${FAKE_NATIONAL_ACTIVE:-0}" = 1 ] && exit 0 || exit 3
EOF
cat > "$FAKE_BIN/pvesm" <<'EOF'
#!/usr/bin/env bash
echo "pvesm $*" >> "$FAKE_LOG"
echo "Volid Format Type Size VMID"
[ "${FAKE_PBS_AGE_H:-1}" = none ] && exit 0
echo "pbs-main:backup/ct/903/$(date -u -d "-${FAKE_PBS_AGE_H:-1} hours" +%Y-%m-%dT%H:%M:%SZ) pbs-ct backup 1000 903"
EOF
cat > "$FAKE_BIN/zpool" <<'EOF'
#!/usr/bin/env bash
[ "${FAKE_POOL_ABSENT:-0}" = 1 ] && { echo "cannot open 'dcpool': no such pool" >&2; exit 1; }
echo "${FAKE_POOL_CAP:-40}%"
EOF
cat > "$FAKE_BIN/zfs" <<'EOF'
#!/usr/bin/env bash
l="$SIM/zfs.list"
has() { awk -v n="$1" '$0 == n { f = 1 } END { exit !f }' "$l"; }
last="${!#}"
case "$1" in
  list) case "$*" in
          *"-t snapshot"*) grep "^$last@" "$l" || true ;;
          *) has "$last" && echo "$last" || exit 1 ;;
        esac ;;
  get) has "$last" || exit 1
       case "$*" in *mountpoint*) echo "/$last" ;; *mounted*) echo yes ;; esac ;;
  snapshot|create) echo "zfs $1 $last" >> "$FAKE_LOG"; echo "$last" >> "$l" ;;
  clone) echo "zfs clone $last" >> "$FAKE_LOG"; echo "$last" >> "$l"
         cp -r "$SIM/db/work" "$SIM/db/${last##*/}" ;;
  destroy) echo "zfs destroy $last" >> "$FAKE_LOG"
           awk -v n="$last" '$0 != n' "$l" > "$l.n" && mv "$l.n" "$l" ;;
  *) exit 1 ;;
esac
EOF
cat > "$FAKE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
ct="${FAKE_CT:?docker outside a CT}"
sv="$SIM/ct/$ct/svc"
volume() {
  [ "$ct" = 902 ] && { echo work; return; }
  # Builtins only, no awk: this runs for every SQL of the script (KI-19, spawn cost of Git Bash).
  local src="" line o i
  while IFS= read -r line; do
    case "$line" in mp[0-9]*": "*) IFS=, read -ra o <<<"${line#*: }"
      for i in "${o[@]:1}"; do [ "$i" = "mp=$PGDATA" ] && src="${o[0]}"; done ;; esac
  done < "$SIM/ct/$ct/config"
  [ -n "$src" ] && echo "${src##*/}" || echo ext4
}
case "$1" in
  info) exit 0 ;;
  ps)
    shift; all=0 svc="" name=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -a) all=1 ;; -q|-aq) [ "$1" = -aq ] && all=1 ;;
        --filter) case "$2" in *service=*) svc="${2##*=}" ;; name=*) name=1 ;; esac; shift ;;
      esac
      shift
    done
    if [ "$name" = 1 ]; then [ -z "${FAKE_IMPORT_CONTAINER:-}" ] || echo "$FAKE_IMPORT_CONTAINER"; exit 0; fi
    for f in "$sv"/*; do
      s="${f##*/}"
      [ -z "$svc" ] || [ "$svc" = "$s" ] || continue
      read -r st < "$f"
      [ "$all" = 1 ] || [ "$st" = running ] && echo "$ct-$s"
    done
    exit 0 ;;
  stop|start)
    a="$1"; shift
    for id in "$@"; do echo "docker $a $id" >> "$FAKE_LOG"
      [ "$a" = stop ] && echo stopped > "$sv/${id#*-}" || echo running > "$sv/${id#*-}"; done ;;
  inspect) c="${!#}"; read -r st < "$sv/${c#*-}"; [ "$st" = running ] && echo healthy || echo starting ;;
  exec)
    shift; DB="" SQL=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -i) shift ;;
        -e) case "$2" in DB=*) DB="${2#DB=}" ;; SQL=*) SQL="${2#SQL=}" ;; esac; shift 2 ;;
        *) break ;;
      esac
    done
    shift  # container
    script="${!#}"
    case "$script" in
      *pg_dump*) echo "pg_dump $ct" >> "$FAKE_LOG"
                 [ "${FAKE_DUMP_BAD:-0}" = 1 ] && printf 'XXXXX' || printf 'PGDMP-fake-archive'; exit 0 ;;
    esac
    db="$SIM/db/$(volume)"
    [ -n "$SQL" ] || SQL="$(cat)"
    case "$SQL" in
      # The SQL files first: public-reset.sql itself reads pg_roles.
      *public_reset.tables*)
        echo "reset $ct" >> "$FAKE_LOG"
        printf '%s\n' "$SQL" | grep '^SET public_reset' > "$SIM/reset-sets"
        [ "${FAKE_RESET_FAIL:-0}" = 1 ] && { echo "ERROR: reset failed" >&2; exit 3; }
        for f in "$db"/staging/*.rows; do mv "$f" "$db/"; done
        echo "PUBLIC_RESET_OK api_keys=$(wc -l < "$db/api_keys.rows" | tr -d ' ')" ;;
      *REANCHORED*) echo "reanchor $ct" >> "$FAKE_LOG"
        echo "REANCHORED 0"; echo "PUBLISHED 21 NOT_PUBLISHED 0 STALE ${FAKE_STALE:-0}" ;;
      *datacommune_temoin*) echo "drop-temoin $ct db=$DB" >> "$FAKE_LOG"; echo TEMOIN_DROPPED ;;
      *"ALTER ROLE"*) echo "password $ct" >> "$FAKE_LOG"; printf '%s\n' "$SQL" > "$SIM/password.sql" ;;
      *"current_user || ' ' || current_database()"*) echo "datacommune datacommune" ;;
      *pg_database_size*) echo "${FAKE_DB_SIZE:-5586488467}" ;;
      *"FROM pg_roles"*) printf '%s\n' ${FAKE_WORK_ROLES:-datacommune wb_reader} ;;
      CHECKPOINT) echo "checkpoint $ct" >> "$FAKE_LOG" ;;
      *attgenerated*) echo "id,payload" ;;
      "SELECT count(*) FROM public."*) t="${SQL##*.}"; wc -l < "$db/$t.rows" | tr -d ' ' ;;
      "COPY public."*" TO STDOUT") t="${SQL#COPY public.}"; t="${t%% *}"
        echo "copy-out $ct $t" >> "$FAKE_LOG"; cat "$db/$t.rows" ;;
      *"CREATE SCHEMA public_reset_rows"*) rm -rf "$db/staging"; mkdir -p "$db/staging"; echo "stage-schema $ct" >> "$FAKE_LOG" ;;
      "CREATE TABLE public_reset_rows."*) t="${SQL#CREATE TABLE public_reset_rows.}"; : > "$db/staging/${t%% *}.rows" ;;
      "COPY public_reset_rows."*) t="${SQL#COPY public_reset_rows.}"; t="${t%% *}"
        cat > "$db/staging/$t.rows"; echo "stage $ct $t" >> "$FAKE_LOG" ;;
      *) echo "unexpected SQL: $SQL" >&2; exit 9 ;;
    esac ;;
  *) exit 1 ;;
esac
EOF
cat > "$FAKE_BIN/curl" <<'EOF'
#!/usr/bin/env bash
out="" url="${!#}"
for a in "$@"; do case "$a" in *dc_anon_*) echo "KEY_ON_ARGV" >> "$FAKE_LOG" ;; esac; done
while [ "$#" -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac; done
echo "curl ${url#http://127.0.0.1:8080}" >> "$FAKE_LOG"
printf '{"statut":"ok","donnees":[{"x":1}]}' > "$out"
case "$url" in *${FAKE_CHECK_FAIL:-no-such-path}*) printf 503 ;; *) printf 200 ;; esac
EOF
cat > "$FAKE_BIN/fake-deploy" <<'EOF'
#!/usr/bin/env bash
echo "deploy $*" >> "$FAKE_LOG"
case " $* " in *" ${FAKE_DEPLOY_FAIL_SHA:-none} "*) exit 81 ;; esac
EOF
cat > "$FAKE_BIN/install" <<'EOF'
#!/usr/bin/env bash
dirs=()
while [ "$#" -gt 0 ]; do case "$1" in -d) shift ;; -m) shift 2 ;; *) dirs+=("$1"); shift ;; esac; done
mkdir -p "${dirs[@]}"
EOF
printf '#!/usr/bin/env bash\nexit 0\n' > "$FAKE_BIN/sleep"
# Free bytes of the host backup directory (df -B1 --output=avail).
cat > "$FAKE_BIN/df" <<'EOF'
#!/usr/bin/env bash
printf 'Avail\n%s\n' "${FAKE_FREE:-7000000000000}"
EOF
printf '#!/usr/bin/env bash\n[ "${FAKE_USB_UNMOUNTED:-0}" = 1 ] && exit 1 || exit 0\n' > "$FAKE_BIN/mountpoint"
printf '#!/usr/bin/env bash\nprintf "1\\n2\\n3\\n"\n' > "$FAKE_BIN/seq"
chmod +x "$FAKE_BIN"/*

export PATH="$FAKE_BIN:$PATH" PGDATA
export PUBLIC_SSH_HOST=fakehost PUBLIC_DEPLOY_SCRIPT="$FAKE_BIN/fake-deploy"
export PUBLIC_DIR="$WORK/ct903/opt" PUBLIC_ENV_FILE="$WORK/ct903/env"
export PUBLIC_BACKUP_HOST_DIR="$WORK/host/usb/datacommune-backups"
export PUBLIC_SWITCH_STATE_DIR="$WORK/host/root/dc-public"

setup() {
  rm -rf "$SIM" "$WORK/ct903" "$WORK/host" "$FAKE_LOG"
  mkdir -p "$SIM/ct/902/svc" "$SIM/ct/903/svc" "$SIM/db/ext4" "$SIM/db/work" "$PUBLIC_DIR" "$WORK/host/usb"
  : > "$FAKE_LOG"
  echo "$OLD_SHA" > "$PUBLIC_DIR/current_sha"
  printf 'CF_TUNNEL_TOKEN=x\nPUBLIC_DC_API_KEY=%s\n' "$SECRET_KEY" > "$PUBLIC_ENV_FILE"
  echo running > "$SIM/ct/902/status"; echo running > "$SIM/ct/903/status"
  echo running > "$SIM/ct/902/svc/db"
  for s in api db frontend caddy cloudflared; do echo running > "$SIM/ct/903/svc/$s"; done
  printf '%s\n' 'arch: amd64' 'hostname: datacommune-work' 'memory: 16384' \
    'mp0: /mnt/usb-8tb/datacommune-sources,mp=/mnt/source-archive' \
    'mp1: /dcpool/work/pg,mp=/var/lib/docker/volumes/datacommune-work_pgdata/_data' \
    'rootfs: local-lvm:vm-902-disk-0,size=200G' 'unprivileged: 1' > "$SIM/ct/902/config"
  printf '%s\n' 'arch: amd64' 'cores: 4' 'hostname: datacommune-public' 'memory: 8192' \
    'net0: name=eth0,bridge=vmbr0,ip=192.0.2.77/24' 'rootfs: local-lvm:vm-903-disk-0,size=60G' \
    'swap: 1024' 'unprivileged: 1' > "$SIM/ct/903/config"
  printf '%s\n' dcpool dcpool/work dcpool/work/pg dcpool/work/tmp > "$SIM/zfs.list"
  local t i
  for t in users api_keys subscriptions saved_searches quota_usage quota_usage_account \
    admin_audit_log stripe_events chat_sessions chat_messages api_request_log signalements; do
    : > "$SIM/db/ext4/$t.rows"; : > "$SIM/db/work/$t.rows"
  done
  printf '900\tpublic-anon\n' > "$SIM/db/ext4/api_keys.rows"
  printf '100001\tpublic-log-1\n100002\tpublic-log-2\n100003\tpublic-log-3\n' > "$SIM/db/ext4/api_request_log.rows"
  printf '900\tpublic-quota\n' > "$SIM/db/ext4/quota_usage.rows"
  for i in $(command seq 1 58); do printf '%s\twork-internal-%s\n' "$i" "$i"; done > "$SIM/db/work/api_keys.rows"
  for i in $(command seq 1 55); do printf '%s\twork-quota-%s\n' "$i" "$i"; done > "$SIM/db/work/quota_usage.rows"
  printf '1\twork-log\n' > "$SIM/db/work/api_request_log.rows"
  printf '1\twork-user\n' > "$SIM/db/work/users.rows"
  cp -r "$SIM/db/ext4" "$SIM/ext4.before"
  cp "$SIM/ct/903/config" "$SIM/config.before"
  unset FAKE_NATIONAL_ACTIVE FAKE_IMPORT_CONTAINER FAKE_PBS_AGE_H FAKE_POOL_ABSENT FAKE_POOL_CAP \
    FAKE_WORK_ROLES FAKE_DUMP_BAD FAKE_RESET_FAIL FAKE_STALE FAKE_CHECK_FAIL FAKE_DEPLOY_FAIL_SHA \
    FAKE_DB_SIZE FAKE_FREE FAKE_USB_UNMOUNTED
}
OUT="" RC=0 STAMP=""
run() {
  OUT="$(bash "$SCRIPT" "$@" 2>&1)"; RC=$?
  STAMP="$(printf '%s\n' "$OUT" | sed -n 's/.*national switch, stamp \([0-9TZ]*\),.*/\1/p' | head -1)"
}
logged() { grep -q -- "$1" "$FAKE_LOG"; }
not_logged() { ! grep -q -- "$1" "$FAKE_LOG"; }
before() { # before <regex1> <regex2>: first match of 1 precedes first match of 2
  local a b
  a="$(grep -n -m1 -- "$1" "$FAKE_LOG" | cut -d: -f1)"; b="$(grep -n -m1 -- "$2" "$FAKE_LOG" | cut -d: -f1)"
  [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]
}
in_out() { grep -q -- "$1" <<<"$OUT"; }
no_write() { # no write of any kind in the log
  ! grep -qE '^(zfs (snapshot|create|clone|destroy)|pct (set|stop|start)|docker (stop|start)|pg_dump|copy-out|checkpoint|deploy|reset|stage)' "$FAKE_LOG"
}
same_config() { cmp -s "$SIM/ct/903/config" "$1"; }
ext4_untouched() { diff -r "$SIM/ext4.before" "$SIM/db/ext4" >/dev/null; }
clone_rows() { cat "$SIM/db/pg-$STAMP/$1.rows"; }

FAILED=()
check() { # check <scenario> <condition...>
  local name="$1"; shift
  if ! "$@"; then FAILED+=("$name"); printf 'FAIL %s\n--- output (rc=%s)\n%s\n--- calls\n%s\n' "$name" "$RC" "$OUT" "$(cat "$FAKE_LOG")" >&2; fi
}

# Groups (KI-19): SWITCH_GROUP=chaine|refus|replis plays only that group, so the backend suite runs
# the three as separate cases, each far under its 300 s; unset, every scenario is played, as before.
# An unknown group fails: nothing is skipped silently.
RAN=()
case "${SWITCH_GROUP:-}" in ""|chaine|refus|replis) ;; *) echo "unknown SWITCH_GROUP: $SWITCH_GROUP" >&2; exit 2 ;; esac
group() { [ -z "${SWITCH_GROUP:-}" ] || [ "$SWITCH_GROUP" = "$1" ]; }
ran() { RAN+=("$1"); }

if group chaine; then
# (a) nominal switch
ran a
setup; run --from-zfs-clone --apply --deploy "$SHA"
check a test "$RC" = 0
check a.ok in_out "NATIONAL_SWITCH_OK $STAMP"
check a.order1 before '^pvesm list' '^pg_dump 903'
check a.order2 before '^pg_dump 903' '^docker stop 903-api'
check a.order3 before '^docker stop 903-api' '^copy-out 903 users'
check a.order4 before '^copy-out 903 api_request_log' '^checkpoint 902'
check a.order5 before '^checkpoint 902' "^zfs snapshot dcpool/work/pg@pub-$STAMP"
check a.order6 before "^zfs snapshot" "^zfs create dcpool/public"
check a.order7 before "^zfs create dcpool/public" "^zfs clone dcpool/public/pg-$STAMP"
check a.order8 before "^zfs clone" '^docker stop 903-db'
check a.order9 before '^docker stop 903-db' '^pct stop 903'
check a.order10 before '^pct stop 903' '^pct set 903'
check a.order11 before '^pct set 903' '^pct start 903'
check a.order12 before '^pct start 903' '^docker start 903-db'
check a.order13 before '^docker start 903-db' '^drop-temoin 903 db=postgres'
check a.order14 before '^drop-temoin' '^password 903'
check a.order15 before '^password 903' '^stage 903 api_keys'
check a.order16 before '^stage 903 api_request_log' '^reset 903'
check a.order17 before '^reset 903' '^reanchor 903'
check a.order18 before '^reanchor 903' "^deploy --dry-run $SHA\$"
check a.order19 before "^deploy --dry-run $SHA\$" "^deploy $SHA\$"
check a.order20 before "^deploy $SHA\$" '^curl /v1/health'
check a.mount grep -qx "mp0: /dcpool/public/pg-$STAMP,mp=$PGDATA" "$SIM/ct/903/config"
check a.memory grep -qx 'memory: 12288' "$SIM/ct/903/config"
check a.keys test "$(clone_rows api_keys)" = "$(printf '900\tpublic-anon')"
check a.no_work_key bash -c '! grep -q work- "$1"' _ "$SIM/db/pg-$STAMP/api_keys.rows"
check a.logs test "$(clone_rows api_request_log)" = "$(cat "$SIM/db/ext4/api_request_log.rows")"
check a.quota test "$(clone_rows quota_usage)" = "$(printf '900\tpublic-quota')"
check a.users test -z "$(clone_rows users)"
check a.ext4 ext4_untouched
check a.sets grep -qx "SET public_reset.tables = 'users,api_keys,subscriptions,saved_searches,quota_usage,quota_usage_account,admin_audit_log,stripe_events,chat_sessions,chat_messages,api_request_log,signalements';" "$SIM/reset-sets"
check a.counts grep -q "api_keys=1,.*quota_usage=1,.*api_request_log=3,signalements=0';" "$SIM/reset-sets"
check a.roles grep -qx "SET public_reset.drop_roles = 'wb_reader';" "$SIM/reset-sets"
check a.password grep -qx "\\\\getenv pw POSTGRES_PASSWORD" "$SIM/password.sql"
check a.reanchor_shown in_out 'publication guard: PUBLISHED 21 NOT_PUBLISHED 0 STALE 0'
check a.dump test "$(head -c 5 "$PUBLIC_BACKUP_HOST_DIR/public-pre-national-$STAMP.dump")" = PGDMP
check a.export test -s "$PUBLIC_BACKUP_HOST_DIR/public-app-rows-$STAMP/api_keys.copy"
check a.verified test -f "$PUBLIC_SWITCH_STATE_DIR/verified-$STAMP"
check a.checks logged '^curl /v1/geocode?q=rue%20de%20Paris%20Saint-Denis&code_postal=97400'
check a.commune logged '^curl /v1/commune?code_insee=44109'
check a.no_work_copy not_logged '^copy-out 902'
check a.no_work_dump not_logged '^pg_dump 902'
check a.no_destroy not_logged '^zfs destroy'
check a.key_argv not_logged KEY_ON_ARGV
check a.key_out bash -c '! grep -q "$1" <<<"$2"' _ "$SECRET_KEY" "$OUT"
A_STAMP="$STAMP"
cp "$SIM/ct/903/config" "$WORK/config.a"
cp -r "$SIM" "$WORK/sim.a"

# (g) --rollback after (a): config of before, release of before
ran g
: > "$FAKE_LOG"; run --from-zfs-clone --rollback "$A_STAMP" --apply
check g test "$RC" = 0
check g.ok in_out "ROLLBACK_OK $A_STAMP"
check g.config same_config "$SIM/config.before"
check g.deploy logged "^deploy --backup-ref file:$PUBLIC_BACKUP_HOST_DIR/public-pre-national-$A_STAMP.dump $OLD_SHA\$"
check g.order before '^pct stop 903' '^pct set 903'
check g.kept grep -qx "dcpool/public/pg-$A_STAMP" "$SIM/zfs.list"
check g.snap grep -qx "dcpool/work/pg@pub-$A_STAMP" "$SIM/zfs.list"
check g.no_destroy not_logged '^zfs destroy'
check g.ext4 ext4_untouched

# (h) --drop-previous: after the rollback, destroys the clone then the snapshot only
ran h
echo "dcpool/public/pg-$A_STAMP@pre-0123456789ab-20261103T000000Z" >> "$SIM/zfs.list"
: > "$FAKE_LOG"; run --from-zfs-clone --drop-previous "$A_STAMP" --apply
check h test "$RC" = 0
check h.order before "^zfs destroy dcpool/public/pg-$A_STAMP\$" "^zfs destroy dcpool/work/pg@pub-$A_STAMP"
check h.pre before "^zfs destroy dcpool/public/pg-$A_STAMP@pre-0123456789ab-20261103T000000Z" "^zfs destroy dcpool/public/pg-$A_STAMP\$"
check h.only test "$(grep -c '^zfs destroy' "$FAKE_LOG")" = 3
check h.list test "$(cat "$SIM/zfs.list")" = "$(printf '%s\n' dcpool dcpool/work dcpool/work/pg dcpool/work/tmp dcpool/public)"
# ... and refused while the clone is mounted
rm -rf "$SIM"; cp -r "$WORK/sim.a" "$SIM"; : > "$FAKE_LOG"
run --from-zfs-clone --drop-previous "$A_STAMP" --apply
check h.mounted test "$RC" = 80
check h.mounted_none not_logged '^zfs destroy'

# (i) second switch over the verified clone of (a), then its rollback: back on the first clone
ran i
: > "$FAKE_LOG"; sleep_stamp="$A_STAMP"
run --from-zfs-clone --apply --deploy "$SHA"
if [ "$STAMP" = "$sleep_stamp" ]; then /usr/bin/sleep 1; run --from-zfs-clone --apply --deploy "$SHA"; fi
check i test "$RC" = 0
check i.mount grep -qx "mp0: /dcpool/public/pg-$STAMP,mp=$PGDATA" "$SIM/ct/903/config"
I_STAMP="$(sed -n 's/^NATIONAL_SWITCH_OK \([0-9TZ]*\).*/\1/p' <<<"$OUT")"
# Without the pre-national dump of the stamp, the rollback lets public-deploy.sh dump.
mv "$PUBLIC_BACKUP_HOST_DIR/public-pre-national-$I_STAMP.dump" "$WORK/moved.dump"
: > "$FAKE_LOG"; run --from-zfs-clone --rollback "$I_STAMP" --apply
check i.rollback test "$RC" = 0
check i.no_ref logged "^deploy $OLD_SHA\$"
check i.config same_config "$WORK/config.a"

fi

if group refus; then
# (b) dry-run by default: surveys only
ran b
setup; run --from-zfs-clone --deploy "$SHA"
check b test "$RC" = 0
check b.out in_out "NATIONAL_SWITCH_DRY_RUN $STAMP"
check b.surveyed logged '^pvesm list'
check b.no_write no_write
check b.config same_config "$SIM/config.before"
check b.files test ! -e "$PUBLIC_BACKUP_HOST_DIR"

# (c) refusals: 80, nothing written
ran c
refused() { # refused <name> <expected message>
  run --from-zfs-clone --apply --deploy "$SHA"
  check "c.$1" test "$RC" = 80
  check "c.$1.msg" in_out "$2"
  check "c.$1.no_write" no_write
  check "c.$1.config" same_config "$SIM/config.before"
}
setup; export FAKE_NATIONAL_ACTIVE=1; refused national "unit dc-national) is active in CT 902"
setup; export FAKE_IMPORT_CONTAINER=abc123; refused import "an import container"
setup; export FAKE_PBS_AGE_H=none; refused no_pbs "no PBS backup of CT 903"
setup; export FAKE_PBS_AGE_H=30; refused old_pbs "older than 24 h"
setup; export FAKE_POOL_ABSENT=1; refused pool_absent "pool dcpool absent"
setup; export FAKE_POOL_CAP=86; refused pool_full "pool dcpool at 86 %"
setup; export FAKE_WORK_ROLES="datacommune wb_reader golden_writer"; refused role "role golden_writer of the work cluster"
setup; export FAKE_DB_SIZE=144000000000 FAKE_FREE=18000000000; refused dump_space "pre-national dump needs ~.* 18000000000 free"
setup; export FAKE_USB_UNMOUNTED=1; refused usb "datacommune-backups) is not mounted\|usb is not mounted"
for f in 0,5 0; do
  setup; PUBLIC_DUMP_SIZE_FACTOR="$f" run --from-zfs-clone --apply --deploy "$SHA"
  check "c.factor_$f" test "$RC" = 2
  check "c.factor_$f.no_write" no_write
done
setup; echo "mp0: /dcpool/public/pg-20261001T000000Z,mp=$PGDATA" >> "$SIM/ct/903/config"
cp "$SIM/ct/903/config" "$SIM/config.before"; refused unverified "pg-20261001T000000Z is mounted in CT 903 and not verified"

# (d) dump without PGDMP header: 81, api never stopped
ran d
setup; export FAKE_DUMP_BAD=1; run --from-zfs-clone --apply --deploy "$SHA"
check d test "$RC" = 81
check d.api not_logged '^docker stop 903-api'
check d.no_zfs not_logged '^zfs snapshot'

fi

if group replis; then
# (e) reset fails after the switch: rolled back on the mounts of before
ran e
setup; export FAKE_RESET_FAIL=1; run --from-zfs-clone --apply --deploy "$SHA"
check e test "$RC" = 82
check e.out in_out "ROLLED_BACK $STAMP"
check e.config same_config "$SIM/config.before"
check e.deploy logged "^deploy --backup-ref file:$PUBLIC_BACKUP_HOST_DIR/public-pre-national-$STAMP.dump $OLD_SHA\$"
check e.no_new_deploy not_logged "^deploy .*$SHA"
check e.ext4 ext4_untouched
check e.kept grep -qx "dcpool/public/pg-$STAMP" "$SIM/zfs.list"

# (f) stale publication: rolled back
ran f
setup; export FAKE_STALE=2; run --from-zfs-clone --apply --deploy "$SHA"
check f test "$RC" = 82
check f.config same_config "$SIM/config.before"
check f.no_new_deploy not_logged "^deploy .*$SHA"

# (f2) a national check fails after the deploy: rolled back
ran f2
setup; export FAKE_CHECK_FAIL=97400; run --from-zfs-clone --apply --deploy "$SHA"
check f2 test "$RC" = 82
check f2.config same_config "$SIM/config.before"

fi

if group refus; then
# (k) dump mode: space of the dump and of the restored base checked before anything
ran k
setup; export FAKE_DB_SIZE=144000000000 FAKE_FREE=18000000000; run
check k test "$RC" = 80
check k.msg in_out "dump mode needs ~.* 18000000000 free"
check k.no_write no_write
setup; PUBLIC_DUMP_SIZE_FACTOR=0,5 run
check k.factor test "$RC" = 2

# (j) the script never deletes outside --drop-previous
ran j
code_lines() { awk -v p="$1" '!/^[[:space:]]*#/ && index($0, p) { n++ } END { print n + 0 }' "$SCRIPT"; }
check j.rm_rf test "$(code_lines 'rm -rf')" = 0
check j.destroy test "$(code_lines 'zfs destroy')" = 3
fi

echo "SCENARIOS ${RAN[*]}"
[ "${#RAN[@]}" -gt 0 ] || FAILED+=(no_scenario)
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "FAILED: ${FAILED[*]}"
  exit 1
fi
echo PUBLIC_SWITCH_TESTS_OK
