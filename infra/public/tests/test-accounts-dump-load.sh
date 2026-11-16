#!/usr/bin/env bash
# Prove accounts-dump.sh and accounts-load.sh (plan 05-01, D-05 to D-07) without a CT,
# without docker, without age and without PostgreSQL.
#
# Fakes come first in PATH. Fake docker answers `docker ps` for the db container of the
# configured compose project and runs `docker exec ... <container> sh -c <script>` FOR REAL
# in a local shell, with POSTGRES_USER/POSTGRES_DB/PGDATA set and fakes of psql, pg_dump and
# pg_restore, so the container scripts, their quoting and their temp directories are
# exercised. Fake psql answers from a simulated cluster ($SIM/db/<database>/<table>.copy):
# advisory locks, the snapshot session (runs its `\!` line with SNAP set), the manifest
# (rows = lines, hash = md5 of the file), COPY out and in, public-reset.sql (moves the
# staged rows into place), roles, alembic revision, CREATE/DROP DATABASE. Fake age
# "encrypts" as its header line plus base64 (no `_` can appear in it), and decrypts back.
# Fake stat answers the mode of the secrets file (NTFS has no modes).
# Cases:
#   (a) dump: encrypted archive on stdout, nothing in clear, last stderr line
#       ACCOUNTS_DUMP ok tables= rows= bytes= seconds= pgdata_free_bytes= largest_table_bytes=;
#       one read-only snapshot session, pg_dump --snapshot --schema-only, every COPY and the
#       manifest in that snapshot; archive content (rows, columns, schema, manifest, env);
#       host and container temp directories removed
#   (b) refusals -> ACCOUNTS_DUMP REFUSED, exit 2, nothing on stdout, no session: exclusive
#       advisory lock, no db container, recipient absent or not age1..., secrets file absent
#       or not mode 600, an argument other than --dry-run (also through
#       SSH_ORIGINAL_COMMAND); --dry-run prints and extracts nothing
#   (c) the extraction list is APP_TABLES read in public-db-restore.sh, same order, then
#       instance_scope_datasets and instance_scope_departements
#   (d) a sentinel secret appears in no output, log or call of either script;
#       --apply-env writes only the named variables, one line each, atomically (mode 600
#       checked where the filesystem keeps modes), and refuses a name absent from the archive
#   (e) --apply refuses (exit 2) when the alembic revision of the archive differs from the
#       target's; plus load --verify (nominal, manifest drift, reset failure: verify database
#       dropped each time) and --apply nominal (work roles dropped, rows replaced)
# Prints ACCOUNTS_DUMP_LOAD_OK only if every case passes. ACCOUNTS_DUMP_SCRIPT and
# ACCOUNTS_LOAD_SCRIPT override the files under test.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
DUMP="${ACCOUNTS_DUMP_SCRIPT:-$REPO/infra/public/accounts-dump.sh}"
LOAD="${ACCOUNTS_LOAD_SCRIPT:-$REPO/infra/public/accounts-load.sh}"
RESTORE="$REPO/infra/public/public-db-restore.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export SIM="$WORK/sim" FAKE_LOG="$WORK/calls.log"
export DB_BIN="$WORK/db-bin"
FAKE_BIN="$WORK/bin"
HOST_TMP="$WORK/host-tmp"
export CT_TMP="$SIM/ct-tmp"
mkdir -p "$FAKE_BIN" "$DB_BIN" "$HOST_TMP" "$CT_TMP" "$SIM/pgdata" "$WORK/etc" "$WORK/target"
REAL_STAT="$(command -v stat)"
export REAL_STAT

SENTINEL="sentinel_secret_4f7a_never_printed"
RECIPIENT="age1$(printf 'q%.0s' $(seq 58))"
REV=110_head_of_the_archive

FAILS=0
fail() { echo "FAIL $*"; FAILS=$((FAILS + 1)); }
pass() { echo "PASS $*"; }

APP=()
read -r -a APP <<<"$(sed -n 's/^readonly APP_TABLES=(\(.*\))$/\1/p' "$RESTORE")"
[ "${#APP[@]}" -gt 0 ] || { echo "FAIL APP_TABLES not found in $RESTORE"; exit 1; }
EXPECTED=("${APP[@]}" instance_scope_datasets instance_scope_departements)

# --- doubles -------------------------------------------------------------------------
cat >"$FAKE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  ps)
    shift; proj="" svc=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --filter)
          case "$2" in
            label=com.docker.compose.project=*) proj="${2#label=com.docker.compose.project=}" ;;
            label=com.docker.compose.service=*) svc="${2#label=com.docker.compose.service=}" ;;
          esac
          shift 2 ;;
        *) shift ;;
      esac
    done
    echo "docker ps $proj $svc" >>"$FAKE_LOG"
    if [ -f "$SIM/db_running" ] && [ "$proj" = "$(cat "$SIM/project")" ] && [ "$svc" = db ]; then echo c0ffee; fi
    exit 0 ;;
  exec)
    shift; envs=()
    while [ "$#" -gt 0 ]; do
      case "$1" in -i) shift ;; -e) envs+=("$2"); shift 2 ;; *) break ;; esac
    done
    [ "$1" = c0ffee ] || { echo "UNEXPECTED docker exec container $1" >>"$FAKE_LOG"; exit 99; }
    shift
    echo "docker exec $1" >>"$FAKE_LOG"
    exec env POSTGRES_USER=datacommune POSTGRES_DB=datacommune PGDATA="$SIM/pgdata" \
      TMPDIR="$CT_TMP" PATH="$DB_BIN:$PATH" "${envs[@]}" "$@" ;;
esac
echo "UNEXPECTED docker $*" >>"$FAKE_LOG"; exit 99
EOF

cat >"$FAKE_BIN/age" <<'EOF'
#!/usr/bin/env bash
mode="" r="" out="" in=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -r) mode=enc; r="$2"; shift 2 ;;
    -d) mode=dec; shift ;;
    -i) shift 2 ;;
    -o) out="$2"; shift 2 ;;
    *) in="$1"; shift ;;
  esac
done
echo "age $mode" >>"$FAKE_LOG"
case "$mode" in
  enc)
    case "$r" in age1*) ;; *) echo "age: bad recipient" >&2; exit 1 ;; esac
    if [ -n "$out" ]; then { echo "age-encryption.org/v1"; base64 -w0; echo; } >"$out"
    else echo "age-encryption.org/v1"; base64 -w0; echo; fi ;;
  dec)
    { IFS= read -r h; [ "$h" = "age-encryption.org/v1" ] || { echo "age: no header" >&2; exit 1; }; base64 -d; } <"${in:-/dev/stdin}" ;;
  *) exit 1 ;;
esac
EOF

cat >"$FAKE_BIN/stat" <<'EOF'
#!/usr/bin/env bash
if [ "$1 $2" = "-c %a" ]; then cat "$SIM/env_mode"; exit 0; fi
exec "$REAL_STAT" "$@"
EOF

cat >"$DB_BIN/psql" <<'EOF'
#!/usr/bin/env bash
db="" sql="" has_c=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -d) db="$2"; shift 2 ;;
    -c) sql="$2"; has_c=1; shift 2 ;;
    -U|-v) shift 2 ;;
    *) shift ;;
  esac
done
[ "$has_c" = 1 ] || sql="$(cat)"
DBDIR="$SIM/db/$db"  # not D: the exported work directory of the dump
log() { echo "psql $db $*" >>"$FAKE_LOG"; }
snap() { # the statement ran in the exported snapshot, read-only
  case "$sql" in *"REPEATABLE READ READ ONLY"*"SET TRANSACTION SNAPSHOT 'fake-snap-1'"*) echo snap=yes ;; *) echo snap=no ;; esac
}
case "$sql" in
  *pg_export_snapshot*)
    case "${PGOPTIONS:-}" in *default_transaction_read_only=on*) ro=yes ;; *) ro=no ;; esac
    case "$sql" in *"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"*) rr=yes ;; *) rr=no ;; esac
    log "session ro=$ro rr=$rr"
    cmd="$(printf '%s\n' "$sql" | sed -n 's/^\\! //p')"
    SNAP=fake-snap-1 sh -c "$cmd" || true
    exit 0 ;;
  *accounts_manifest.tables*)
    tables="$(printf '%s\n' "$sql" | sed -n "s/^SET accounts_manifest.tables = '\(.*\)';\$/\1/p")"
    log "manifest $(snap)"
    for t in ${tables//,/ }; do
      f="$DBDIR/$t.copy"
      [ -f "$f" ] || { echo "ERROR: relation public.$t does not exist" >&2; exit 3; }
      n="$(wc -l <"$f" | tr -d ' ')"; h="$(md5sum <"$f" | cut -c1-32)"
      if [ -f "$SIM/drift" ] && [ "$t" = users ]; then h=0123456789abcdef0123456789abcdef; fi
      printf 'table\t%s\t%s\t%s\t%s\n' "$t" "$n" "$h" "id,v"
    done ;;
  *"COPY (SELECT"*"TO STDOUT"*)
    t="$(printf '%s\n' "$sql" | sed -n 's/.*FROM public\.\([a-z_]*\)) TO STDOUT.*/\1/p')"
    log "copy-out $t $(snap)"
    cat "$DBDIR/$t.copy" ;;
  *"COPY public_reset_rows."*"FROM STDIN"*)
    t="$(printf '%s\n' "$sql" | sed -n 's/.*COPY public_reset_rows\.\([a-z_]*\) .*/\1/p')"
    mkdir -p "$DBDIR/staged"; cat >"$DBDIR/staged/$t.copy"
    log "copy-in $t" ;;
  *PUBLIC_RESET_OK*)
    drop="$(printf '%s\n' "$sql" | sed -n "s/^SET public_reset.drop_roles = '\(.*\)';\$/\1/p")"
    keep="$(printf '%s\n' "$sql" | sed -n "s/^SET public_reset.keep_roles = '\(.*\)';\$/\1/p")"
    log "reset drop=$drop keep=$keep"
    [ -f "$SIM/reset_fails" ] && { echo "ERROR: public.users differs from the exported rows" >&2; exit 3; }
    for f in "$DBDIR"/staged/*.copy; do mv "$f" "$DBDIR/"; done
    echo "PUBLIC_RESET_OK api_keys=$(wc -l <"$DBDIR/api_keys.copy" | tr -d ' ')" ;;
  *pg_total_relation_size*) log "largest $(snap)"; echo "${FAKE_LARGEST:-8192}" ;;
  *alembic_version*) log "alembic"; cat "$DBDIR/alembic" ;;
  *pg_locks*) log "locks"; echo "${FAKE_BUSY:-0}" ;;
  *pg_roles*) log "roles"; printf '%s\n' ${FAKE_ROLES:-datacommune} ;;
  *"DROP DATABASE"*)
    n="$(printf '%s\n' "$sql" | sed -n 's/.*DROP DATABASE IF EXISTS \([a-z_]*\).*/\1/p')"
    rm -rf "$SIM/db/$n"; log "drop-db $n" ;;
  *"CREATE DATABASE"*)
    n="$(printf '%s\n' "$sql" | sed -n 's/.*CREATE DATABASE \([a-z_]*\) .*/\1/p')"
    mkdir -p "$SIM/db/$n"; log "create-db $n" ;;
  *"CREATE SCHEMA public_reset_rows"*|*"CREATE TABLE public_reset_rows."*) log ddl ;;
  *) log "UNEXPECTED $(printf '%s' "$sql" | head -c 80 | tr '\n' ' ')"; exit 99 ;;
esac
EOF

cat >"$DB_BIN/pg_dump" <<'EOF'
#!/usr/bin/env bash
snap="" f="" so=no
for a in "$@"; do
  case "$a" in --snapshot=*) snap="${a#--snapshot=}" ;; --schema-only) so=yes ;; esac
done
while [ "$#" -gt 0 ]; do case "$1" in -f) f="$2"; shift 2 ;; *) shift ;; esac; done
case "${PGOPTIONS:-}" in *default_transaction_read_only=on*) ro=yes ;; *) ro=no ;; esac
echo "pg_dump snapshot=$snap schema_only=$so ro=$ro" >>"$FAKE_LOG"
printf 'PGDMP fake schema\n' >"${f:-/dev/stdout}"
EOF

cat >"$DB_BIN/pg_restore" <<'EOF'
#!/usr/bin/env bash
db="" so=no
for a in "$@"; do case "$a" in --schema-only) so=yes ;; esac; done
while [ "$#" -gt 0 ]; do case "$1" in -d) db="$2"; shift 2 ;; *) shift ;; esac; done
echo "pg_restore db=$db schema_only=$so" >>"$FAKE_LOG"
[ -f "$SIM/restore_fails" ] && { cat >/dev/null; exit 1; }
mkdir -p "$SIM/db/$db"; cat >"$SIM/db/$db/schema.restored"
EOF
chmod +x "$FAKE_BIN"/* "$DB_BIN"/*
export PATH="$FAKE_BIN:$PATH"

# --- simulated production host ----------------------------------------------------------
CONFIG="$WORK/etc/accounts-backup.env"
ENV_FILE="$SIM/opt/datacommune.env"
write_config() { # write_config <recipient>
  {
    echo "ACCOUNTS_PROJECT=datacommune-public"
    echo "ACCOUNTS_ENV_FILE=$ENV_FILE"
    [ -z "$1" ] || echo "ACCOUNTS_AGE_RECIPIENT=$1"
    echo "ACCOUNTS_SHARE_DIR=$REPO/infra/public"
    echo "ACCOUNTS_SHA_FILE=$SIM/current_sha"
  } >"$CONFIG"
}
reset_sim() {
  rm -rf "$SIM/db" "$SIM/opt" "$SIM/drift" "$SIM/reset_fails" "$SIM/restore_fails"
  mkdir -p "$SIM/db/datacommune" "$SIM/opt"
  : >"$SIM/db_running"; echo datacommune-public >"$SIM/project"; echo 600 >"$SIM/env_mode"
  echo 0e9aef0c8dcb7c1965110b6b09058920d84624dc >"$SIM/current_sha"
  local t i
  for t in "${EXPECTED[@]}"; do : >"$SIM/db/datacommune/$t.copy"; done
  for i in 1 2; do printf '%s\tuser %s\n' "$((500 + i))" "$i" >>"$SIM/db/datacommune/users.copy"; done
  printf '900\tanon\n901\tpersonal\n' >"$SIM/db/datacommune/api_keys.copy"
  printf '1\tGET /v1/health\n2\tGET /v1/commune\n3\tGET /v1/geocode\n' >"$SIM/db/datacommune/api_request_log.copy"
  printf 'dvf\t2026-11-01 00:00:00+00\n' >"$SIM/db/datacommune/instance_scope_datasets.copy"
  printf '44\t2026-11-01 00:00:00+00\n' >"$SIM/db/datacommune/instance_scope_departements.copy"
  echo "$REV" >"$SIM/db/datacommune/alembic"
  printf 'POSTGRES_PASSWORD=%s\nPUBLIC_DC_API_KEY=dc_anon_archived\nexport STRIPE_KEY=archived_stripe\n' "$SENTINEL" >"$ENV_FILE"
  write_config "$RECIPIENT"
  : >"$FAKE_LOG"
}
total_rows() { cat "$SIM"/db/datacommune/*.copy | wc -l | tr -d ' '; }
dump() { # dump [args...]: stdout to $WORK/out.age, stderr to $WORK/err
  RC=0
  DC_ACCOUNTS_CONFIG="$CONFIG" TMPDIR="$HOST_TMP" bash "$DUMP" "$@" >"$WORK/out.age" 2>"$WORK/err" || RC=$?
}
temp_clean() { [ -z "$(ls -A "$HOST_TMP")" ] && [ -z "$(ls -A "$CT_TMP")" ]; }
no_sentinel() { # no_sentinel <file...>
  ! grep -qF "$SENTINEL" "$@" "$FAKE_LOG" 2>/dev/null
}
last_err() { tr -d '\r' <"$WORK/err" | tail -n 1; }

# (a) nominal dump --------------------------------------------------------------------------
reset_sim
dump
rows="$(total_rows)"
bytes="$(wc -c <"$WORK/out.age" | tr -d ' ')"
X="$WORK/x"; rm -rf "$X"; mkdir -p "$X"
ok=1
[ "$RC" = 0 ] || ok=0
[[ "$(last_err)" =~ ^ACCOUNTS_DUMP\ ok\ tables=14\ rows=$rows\ bytes=$bytes\ seconds=[0-9]+\ pgdata_free_bytes=[0-9]+\ largest_table_bytes=8192$ ]] || ok=0
[ "$(head -n 1 "$WORK/out.age")" = "age-encryption.org/v1" ] || ok=0
age -d "$WORK/out.age" >"$WORK/archive.tar" 2>/dev/null && tar -C "$X" -xf "$WORK/archive.tar" || ok=0
for t in "${EXPECTED[@]}"; do
  cmp -s "$X/rows/$t.copy" "$SIM/db/datacommune/$t.copy" || { ok=0; echo "  rows of $t differ"; }
  [ "$(cat "$X/rows/$t.columns" 2>/dev/null)" = "id,v" ] || { ok=0; echo "  columns of $t"; }
done
[ "$(head -c 5 "$X/schema.dump" 2>/dev/null)" = PGDMP ] || ok=0
cmp -s "$X/env/datacommune.env" "$ENV_FILE" || { ok=0; echo "  env file not archived as is"; }
M="$X/manifest.tsv"
grep -qx "alembic	$REV" "$M" && grep -qx "sha	0e9aef0c8dcb7c1965110b6b09058920d84624dc" "$M" &&
  grep -qx "largest_table_bytes	8192" "$M" && grep -qE '^pgdata_free_bytes	[0-9]+$' "$M" &&
  grep -qE '^host	.+' "$M" && grep -qE '^date	[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$' "$M" || { ok=0; echo "  manifest facts"; }
[ "$(sed -n 's/^env_name	//p' "$M" | tr '\n' ' ')" = "POSTGRES_PASSWORD PUBLIC_DC_API_KEY STRIPE_KEY " ] || { ok=0; echo "  env names"; }
grep -q "psql datacommune session ro=yes rr=yes" "$FAKE_LOG" || { ok=0; echo "  no read-only snapshot session"; }
grep -q "pg_dump snapshot=fake-snap-1 schema_only=yes ro=yes" "$FAKE_LOG" || { ok=0; echo "  pg_dump not on the snapshot"; }
! grep -q "snap=no" "$FAKE_LOG" || { ok=0; echo "  a statement outside the snapshot"; }
[ "$(grep -c '^psql datacommune copy-out ' "$FAKE_LOG")" = 14 ] || { ok=0; echo "  copy-out count"; }
temp_clean || { ok=0; echo "  temp directories left"; }
no_sentinel "$WORK/out.age" "$WORK/err" || { ok=0; echo "  sentinel printed"; }
if [ "$ok" = 1 ]; then pass "(a) nominal dump -> encrypted archive, ACCOUNTS_DUMP ok, temp removed"
else fail "(a) rc=$RC $(tail -n 3 "$WORK/err" | tr '\n' ' ')"; fi
cp "$WORK/archive.tar" "$WORK/good.tar" 2>/dev/null || : >"$WORK/good.tar"

# (c) extraction list = APP_TABLES then the hosted scope ---------------------------------
got="$(grep '^table	' "$M" 2>/dev/null | cut -f2 | tr '\n' ' ')"
want="$(printf '%s ' "${EXPECTED[@]}")"
copied="$(sed -n 's/^psql datacommune copy-out \([a-z_]*\) .*/\1/p' "$FAKE_LOG" | tr '\n' ' ')"
files="$(cd "$X/rows" 2>/dev/null && ls ./*.copy 2>/dev/null | sed 's#^\./##; s/\.copy$//' | sort | tr '\n' ' ')"
if [ "$got" = "$want" ] && [ "$copied" = "$want" ] && [ "$files" = "$(printf '%s\n' "${EXPECTED[@]}" | sort | tr '\n' ' ')" ]; then
  pass "(c) extraction list = APP_TABLES of public-db-restore.sh + instance_scope_*, same order"
else fail "(c) list differs: manifest='$got' copied='$copied' expected='$want'"; fi
reset_sim
dump --dry-run
listed="$(sed -n 's/^table //p' "$WORK/out.age" | tr '\n' ' ')"
if [ "$RC" = 0 ] && [ "$listed" = "$want" ] && ! grep -q session "$FAKE_LOG" && ! grep -q "^age " "$FAKE_LOG" &&
  grep -q '^container c0ffee$' "$WORK/out.age" && grep -qE '^recipient age1q{6}.* sha256=[0-9a-f]{12}$' "$WORK/out.age"; then
  pass "(c) --dry-run lists the same tables, extracts nothing"
else fail "(c) --dry-run rc=$RC $(tr '\n' ' ' <"$WORK/out.age") $(last_err)"; fi

# (b) refusals ---------------------------------------------------------------------------
refused() { # refused <label>
  if [ "$RC" = 2 ] && [ ! -s "$WORK/out.age" ] && [ "$(last_err)" = "ACCOUNTS_DUMP REFUSED" ] &&
    ! grep -q "session" "$FAKE_LOG" && ! grep -q "^age " "$FAKE_LOG"; then pass "(b) $1 -> REFUSED"
  else fail "(b) $1 rc=$RC $(last_err)"; fi
}
reset_sim; FAKE_BUSY=1 dump; refused "exclusive advisory lock granted"
reset_sim; rm -f "$SIM/db_running"; dump; refused "no db container"
reset_sim; write_config ""; dump; refused "recipient absent"
reset_sim; write_config "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5"; dump; refused "recipient not age1"
reset_sim; rm -f "$ENV_FILE"; dump; refused "secrets file absent"
reset_sim; echo 644 >"$SIM/env_mode"; dump; refused "secrets file mode 644"
reset_sim; dump --force; refused "argument --force"
reset_sim; RC=0; SSH_ORIGINAL_COMMAND="cat /etc/shadow" DC_ACCOUNTS_CONFIG="$CONFIG" TMPDIR="$HOST_TMP" \
  bash "$DUMP" >"$WORK/out.age" 2>"$WORK/err" || RC=$?
refused "forced command with another command"
reset_sim; RC=0; SSH_ORIGINAL_COMMAND="--dry-run" DC_ACCOUNTS_CONFIG="$CONFIG" TMPDIR="$HOST_TMP" \
  bash "$DUMP" >"$WORK/out.age" 2>"$WORK/err" || RC=$?
if [ "$RC" = 0 ] && grep -q '^table users$' "$WORK/out.age" && ! grep -q session "$FAKE_LOG"; then
  pass "(b) forced command --dry-run -> dry run"
else fail "(b) forced command --dry-run rc=$RC"; fi

# --- load ----------------------------------------------------------------------------------
LCONFIG="$WORK/etc/load.env"
TARGET_ENV="$WORK/target/datacommune.env"
write_lconfig() { # write_lconfig <project>
  printf 'ACCOUNTS_PROJECT=%s\nACCOUNTS_ENV_FILE=%s\nACCOUNTS_SHARE_DIR=%s\n' "$1" "$TARGET_ENV" "$REPO/infra/public" >"$LCONFIG"
}
load() { # load [args...] < $WORK/good.tar: stdout $WORK/lout, stderr $WORK/lerr
  RC=0
  DC_ACCOUNTS_CONFIG="$LCONFIG" TMPDIR="$HOST_TMP" bash "$LOAD" "$@" <"$WORK/good.tar" >"$WORK/lout" 2>"$WORK/lerr" || RC=$?
}
lout_last() { tr -d '\r' <"$WORK/lout" | tail -n 1; }
setup_target() { # served database of the target: internal rows, revision $1
  reset_sim
  echo datacommune-work >"$SIM/project"; write_lconfig datacommune-work
  rm -rf "$SIM/db/datacommune"; mkdir -p "$SIM/db/datacommune"
  printf '1\tinternal key\n2\tinternal key\n3\tinternal key\n' >"$SIM/db/datacommune/api_keys.copy"
  echo "$1" >"$SIM/db/datacommune/alembic"
}

# verify, nominal
setup_target "$REV"
load --verify
n_rows="$(grep '^table	' "$M" | awk -F'\t' '{ s += $3 } END { print s + 0 }')"
ok=1
[ "$RC" = 0 ] || ok=0
[[ "$(lout_last)" =~ ^ACCOUNTS_VERIFY\ ok\ tables=14\ rows=$n_rows\ env_names=3\ seconds=[0-9]+$ ]] || ok=0
grep -q "psql postgres drop-db dc_accounts_verify" "$FAKE_LOG" && grep -q "psql postgres create-db dc_accounts_verify" "$FAKE_LOG" || ok=0
grep -q "pg_restore db=dc_accounts_verify schema_only=yes" "$FAKE_LOG" || ok=0
[ "$(grep -c '^psql dc_accounts_verify copy-in ' "$FAKE_LOG")" = 14 ] || ok=0
grep -q "psql dc_accounts_verify reset drop= keep=datacommune$" "$FAKE_LOG" || ok=0
grep -q "psql dc_accounts_verify manifest" "$FAKE_LOG" || ok=0
[ ! -d "$SIM/db/dc_accounts_verify" ] || { ok=0; echo "  verify database left"; }
[ "$(cat "$SIM/db/datacommune/api_keys.copy")" = "$(printf '1\tinternal key\n2\tinternal key\n3\tinternal key')" ] || { ok=0; echo "  served database touched"; }
temp_clean || { ok=0; echo "  temp left"; }
no_sentinel "$WORK/lout" "$WORK/lerr" || { ok=0; echo "  sentinel printed"; }
if [ "$ok" = 1 ]; then pass "(e) load --verify -> ACCOUNTS_VERIFY ok, verify database dropped, served base untouched"
else fail "(e) load --verify rc=$RC $(lout_last) $(tail -n 2 "$WORK/lerr" | tr '\n' ' ')"; fi

# verify, manifest drift and reset failure
for what in drift reset_fails restore_fails; do
  setup_target "$REV"; : >"$SIM/$what"
  load --verify
  if [ "$RC" = 1 ] && [[ "$(lout_last)" =~ ^ACCOUNTS_VERIFY\ FAILED\  ]] && [ ! -d "$SIM/db/dc_accounts_verify" ]; then
    pass "(e) load --verify with $what -> FAILED, verify database dropped"
  else fail "(e) load --verify with $what rc=$RC $(lout_last)"; fi
done
setup_target "$REV"; : >"$SIM/drift"; load --verify
grep -q "MISMATCH users" "$WORK/lerr" && ! grep -q "MISMATCH api_keys" "$WORK/lerr" &&
  pass "(e) the drift names the table" || fail "(e) drift not named: $(tr '\n' ' ' <"$WORK/lerr")"

# apply, nominal: work roles dropped, rows replaced
setup_target "$REV"
FAKE_ROLES="datacommune wb_reader" load --apply --drop-roles wb_reader
if [ "$RC" = 0 ] && [[ "$(lout_last)" =~ ^ACCOUNTS_RESTORE_OK\ tables=14\ rows=$n_rows\ api_keys=2\ seconds=[0-9]+$ ]] &&
  grep -q "psql datacommune reset drop=wb_reader keep=datacommune$" "$FAKE_LOG" &&
  cmp -s "$SIM/db/datacommune/api_keys.copy" "$X/rows/api_keys.copy" && ! grep -q "create-db" "$FAKE_LOG"; then
  pass "(e) load --apply -> ACCOUNTS_RESTORE_OK, internal keys replaced, work role dropped"
else fail "(e) load --apply rc=$RC $(lout_last) $(tail -n 2 "$WORK/lerr" | tr '\n' ' ')"; fi

# apply refused on another revision
setup_target 109_older_head
load --apply
if [ "$RC" = 2 ] && [ "$(lout_last)" = "ACCOUNTS_RESTORE REFUSED" ] && ! grep -q -e copy-in -e reset "$FAKE_LOG" &&
  [ "$(wc -l <"$SIM/db/datacommune/api_keys.copy" | tr -d ' ')" = 3 ]; then
  pass "(e) load --apply with another alembic revision -> REFUSED, nothing staged"
else fail "(e) load --apply revision rc=$RC $(lout_last)"; fi

# apply --dry-run writes nothing
setup_target "$REV"
load --apply --dry-run
if [ "$RC" = 0 ] && ! grep -q -e copy-in -e reset -e create-db "$FAKE_LOG" && grep -q '^ACCOUNTS_RESTORE dry-run' "$WORK/lout"; then
  pass "(e) load --apply --dry-run -> nothing written"
else fail "(e) load --apply --dry-run rc=$RC $(lout_last)"; fi

# (d) --apply-env -----------------------------------------------------------------------------
setup_target "$REV"
printf 'KEEP_ME=1\nPUBLIC_DC_API_KEY=dc_anon_old\n# comment kept\n' >"$TARGET_ENV"
load --apply-env PUBLIC_DC_API_KEY,POSTGRES_PASSWORD
ok=1
[ "$RC" = 0 ] && [ "$(lout_last)" = "ACCOUNTS_ENV_OK names=PUBLIC_DC_API_KEY,POSTGRES_PASSWORD" ] || ok=0
[ "$(grep -c '^KEEP_ME=1$' "$TARGET_ENV")" = 1 ] && [ "$(grep -c '^# comment kept$' "$TARGET_ENV")" = 1 ] || ok=0
[ "$(grep -c '^PUBLIC_DC_API_KEY=' "$TARGET_ENV")" = 1 ] && grep -qx 'PUBLIC_DC_API_KEY=dc_anon_archived' "$TARGET_ENV" || ok=0
[ "$(grep -cx "POSTGRES_PASSWORD=$SENTINEL" "$TARGET_ENV")" = 1 ] || ok=0
! grep -q STRIPE_KEY "$TARGET_ENV" || { ok=0; echo "  a name not asked for was written"; }
[ "$(ls -A "$WORK/target")" = datacommune.env ] || { ok=0; echo "  temp file left next to the env file"; }
no_sentinel "$WORK/lout" "$WORK/lerr" || { ok=0; echo "  sentinel printed"; }
# Mode 600 only where the filesystem keeps modes (not NTFS under Git Bash).
probe="$WORK/mode-probe"; : >"$probe"; chmod 600 "$probe"
if [ "$("$REAL_STAT" -c %a "$probe")" = 600 ]; then
  [ "$("$REAL_STAT" -c %a "$TARGET_ENV")" = 600 ] || { ok=0; echo "  env file not mode 600"; }
  modes="checked"
else modes="not kept by this filesystem"; fi
if [ "$ok" = 1 ]; then pass "(d) --apply-env -> only the named variables, one line each (mode 600: $modes)"
else fail "(d) --apply-env rc=$RC $(lout_last)"; fi
cp "$TARGET_ENV" "$WORK/env.before"
load --apply-env NOT_IN_ARCHIVE
if [ "$RC" = 2 ] && cmp -s "$TARGET_ENV" "$WORK/env.before" && [ "$(ls -A "$WORK/target")" = datacommune.env ]; then
  pass "(d) --apply-env with a name absent from the archive -> refused, env file unchanged"
else fail "(d) --apply-env absent name rc=$RC"; fi
load --apply-env 'X;rm'
[ "$RC" = 2 ] && cmp -s "$TARGET_ENV" "$WORK/env.before" && pass "(d) --apply-env with an odd name -> refused" ||
  fail "(d) --apply-env odd name rc=$RC"
setup_target "$REV"
load --verify --apply-env POSTGRES_PASSWORD
[ "$RC" = 2 ] && pass "(d) --verify with --apply-env -> usage" || fail "(d) --verify --apply-env rc=$RC"

# (d) the sentinel never left the archive in any run of this file
if ! grep -qF "$SENTINEL" "$FAKE_LOG" "$WORK/err" "$WORK/lerr" "$WORK/lout"; then
  pass "(d) sentinel absent from every output and call log"
else fail "(d) sentinel found in an output"; fi

for s in "$DUMP" "$LOAD"; do
  if bash -n "$s" 2>/dev/null; then pass "bash -n $(basename "$s")"; else fail "bash -n $(basename "$s")"; fi
done

if [ "$FAILS" -eq 0 ]; then
  echo ACCOUNTS_DUMP_LOAD_OK
else
  echo "$FAILS case(s) failed"
  exit 1
fi
