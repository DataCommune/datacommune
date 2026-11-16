#!/usr/bin/env bash
# Encrypted backup of the accounts, keys and settings of the production host (plan 05-01,
# D-05, D-06, D-08). Installed on the production host as /usr/local/sbin/dc-accounts-dump and
# run there as root: by `pct exec <ctid>` from the hypervisor today (public container), by ssh with a key
# restricted to this one command after the switch to the bought server.
#
#   dc-accounts-dump              archive on stdout, report on stderr
#   dc-accounts-dump --dry-run    tables, container and recipient; nothing is extracted
#   (forced ssh command: the same argument is read from SSH_ORIGINAL_COMMAND)
#
# Settings: /etc/datacommune/accounts-backup.env (DC_ACCOUNTS_CONFIG overrides the path),
# plain KEY=value lines, no secret: ACCOUNTS_PROJECT (compose project of the db container),
# ACCOUNTS_ENV_FILE (secrets of the stack, mode 600), ACCOUNTS_AGE_RECIPIENT (age public key,
# age1...; the identity that decrypts never lives on this host), ACCOUNTS_SHARE_DIR (holds
# accounts-manifest.sql), ACCOUNTS_SHA_FILE (served release, optional).
#
# Refused (exit 2, nothing on stdout): an argument other than --dry-run, a setting missing or
# odd, the recipient not age1..., the secrets file absent or not mode 600, no db container,
# an exclusive advisory lock granted in the served database (import, publication lock
# 0x434F523230, maintenance), as workbase-backup.sh refuses.
# Extraction: one psql session, REPEATABLE READ READ ONLY, exports its snapshot and holds it
# while a script of the container runs, every statement of which sets that snapshot:
# pg_dump --schema-only of the whole database, the manifest (accounts-manifest.sql: rows,
# fingerprint and columns per table), `COPY (SELECT <columns> FROM <table>) TO STDOUT` per
# table, the alembic revision and the largest relation; then the free space of PGDATA. The
# list is APP_TABLES of public-db-restore.sh, same order (test-accounts-dump-load.sh fails
# on any difference), then the hosted scope instance_scope_*.
# Archive (tar, encrypted by `age -r` here, before it leaves the host):
#   rows/<table>.columns, rows/<table>.copy   column list and COPY text (format of the export
#                                             of public-db-restore.sh)
#   schema.dump                               pg_dump -Fc --schema-only
#   manifest.tsv                              table lines of the manifest, then alembic,
#                                             largest_table_bytes, pgdata_free_bytes, sha,
#                                             host, date, env_name (names only)
#   env/datacommune.env                       the secrets file, copied as is, never printed
# Every temp directory is private (umask 077) and removed on exit, on this host and in the
# container. No DSN and no password is read: psql and pg_dump run in the db container on its
# local socket with its own environment.
# Last stderr line:
#   ACCOUNTS_DUMP ok tables=<n> rows=<n> bytes=<n> seconds=<n> pgdata_free_bytes=<n> largest_table_bytes=<n>
#   ACCOUNTS_DUMP REFUSED | ACCOUNTS_DUMP FAILED
# Exit 0, 2 refused, 1 failed.
set -euo pipefail
umask 077

# Application tables: APP_TABLES of infra/public/public-db-restore.sh, same order (parents
# first), then the hosted scope. Kept identical by infra/public/tests/test-accounts-dump-load.sh.
readonly TABLES=(users api_keys subscriptions saved_searches quota_usage quota_usage_account admin_audit_log stripe_events chat_sessions chat_messages api_request_log signalements instance_scope_datasets instance_scope_departements)

refuse() { echo "$1" >&2; echo "ACCOUNTS_DUMP REFUSED" >&2; exit 2; }
fail() { echo "$1" >&2; echo "ACCOUNTS_DUMP FAILED" >&2; exit 1; }

# Arguments, or the command asked through a forced ssh command.
args=("$@")
if [ "${#args[@]}" -eq 0 ] && [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  [ "$SSH_ORIGINAL_COMMAND" = --dry-run ] || refuse "forced command: only --dry-run is accepted"
  args=(--dry-run)
fi
dry=0
case "${#args[@]}:${args[0]:-}" in
  0:) ;;
  1:--dry-run) dry=1 ;;
  *) refuse "usage: dc-accounts-dump [--dry-run]" ;;
esac

config="${DC_ACCOUNTS_CONFIG:-/etc/datacommune/accounts-backup.env}"
[ -f "$config" ] || refuse "settings file $config absent"
ACCOUNTS_PROJECT="" ACCOUNTS_ENV_FILE="" ACCOUNTS_AGE_RECIPIENT="" ACCOUNTS_SHARE_DIR="" ACCOUNTS_SHA_FILE=""
# Plain KEY=value lines only: the file is read, never sourced.
while IFS='=' read -r key value; do
  value="${value%$'\r'}"
  case "$key" in
    ACCOUNTS_PROJECT|ACCOUNTS_ENV_FILE|ACCOUNTS_AGE_RECIPIENT|ACCOUNTS_SHARE_DIR|ACCOUNTS_SHA_FILE)
      printf -v "$key" '%s' "$value" ;;
  esac
done <"$config"
plain_path() { [[ "$1" =~ ^/[A-Za-z0-9/._-]+$ ]] && [[ "$1" != *..* ]]; }
[[ "$ACCOUNTS_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || refuse "ACCOUNTS_PROJECT missing or odd"
plain_path "$ACCOUNTS_ENV_FILE" || refuse "ACCOUNTS_ENV_FILE is not a plain absolute path"
plain_path "$ACCOUNTS_SHARE_DIR" || refuse "ACCOUNTS_SHARE_DIR is not a plain absolute path"
[[ "$ACCOUNTS_AGE_RECIPIENT" =~ ^age1[02-9ac-hj-np-z]{58}$ ]] ||
  refuse "ACCOUNTS_AGE_RECIPIENT missing or not an age X25519 recipient (age1...)"
[ -f "$ACCOUNTS_SHARE_DIR/accounts-manifest.sql" ] || refuse "$ACCOUNTS_SHARE_DIR/accounts-manifest.sql absent"
[ -f "$ACCOUNTS_ENV_FILE" ] || refuse "secrets file $ACCOUNTS_ENV_FILE absent"
mode="$(stat -c %a "$ACCOUNTS_ENV_FILE")" || refuse "mode of $ACCOUNTS_ENV_FILE unreadable"
[ "$mode" = 600 ] || refuse "secrets file $ACCOUNTS_ENV_FILE is mode $mode, not 600"

c="$(docker ps -q --filter "label=com.docker.compose.project=$ACCOUNTS_PROJECT" \
  --filter label=com.docker.compose.service=db | head -n 1)"
[ -n "$c" ] || refuse "no running db container in project $ACCOUNTS_PROJECT"

# psql of the db container, local socket, served database, read-only transactions.
IN_DB='PGOPTIONS="-c default_transaction_read_only=on"; export PGOPTIONS
exec psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
busy="$(printf '%s\n' "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND mode = 'ExclusiveLock'
  AND granted AND database = (SELECT oid FROM pg_database WHERE datname = current_database());" |
  docker exec -i "$c" sh -c "$IN_DB" | tr -d '\r ')" || refuse "advisory lock state unreadable"
[[ "$busy" =~ ^[0-9]+$ ]] || refuse "advisory lock state unreadable"
[ "$busy" = 0 ] || refuse "an import, publication or maintenance holds an exclusive advisory lock"

if [ "$dry" = 1 ]; then
  printf 'table %s\n' "${TABLES[@]}"
  echo "container $c"
  fp="$(printf '%s' "$ACCOUNTS_AGE_RECIPIENT" | sha256sum | cut -c1-12)"
  echo "recipient ${ACCOUNTS_AGE_RECIPIENT:0:12}... sha256=$fp"
  echo "ACCOUNTS_DUMP dry-run tables=${#TABLES[@]}" >&2
  exit 0
fi

# Script of the container, run by `\!` inside the snapshot session (SNAP, D and TABLES_CSV in
# its environment). Every statement starts a READ ONLY transaction on the exported snapshot.
# It writes $D/out/ and marks the end with WORK_OK: psql does not fail on a failed `\!`.
read -r -d '' WORK <<'WORK' || true
set -eu
o="$D/out"
mkdir -p "$o/rows"
q() { psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"; }
in_snap() {
  printf '%s\n' "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" "SET TRANSACTION SNAPSHOT '$SNAP';" \
    "SET TimeZone = 'UTC';" "SET DateStyle = 'ISO, YMD';" "SET IntervalStyle = 'postgres';" \
    "SET extra_float_digits = 1;" "SET bytea_output = 'hex';"
  cat
  printf '%s\n' "COMMIT;"
}
pg_dump --snapshot="$SNAP" -Fc --schema-only -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$o/schema.dump"
{ printf "SET accounts_manifest.tables = '%s';\n" "$TABLES_CSV"; cat "$D/manifest.sql"; } | in_snap | q | tr -d '\r' >"$o/manifest.lines"
for t in $(printf '%s' "$TABLES_CSV" | tr ',' ' '); do
  line="$(awk -F '\t' -v t="$t" '$1 == "table" && $2 == t' "$o/manifest.lines")"
  [ -n "$line" ] || { echo "no manifest line for $t" >&2; exit 3; }
  n="$(printf '%s\n' "$line" | cut -f3)"
  cols="$(printf '%s\n' "$line" | cut -f5)"
  printf '%s\n' "$cols" >"$o/rows/$t.columns"
  printf 'COPY (SELECT %s FROM public.%s) TO STDOUT;\n' "$cols" "$t" | in_snap | q >"$o/rows/$t.copy"
  got="$(wc -l <"$o/rows/$t.copy" | tr -d ' ')"
  [ "$got" = "$n" ] || { echo "$t: $got lines exported, manifest counts $n" >&2; exit 3; }
done
rev="$(echo "SELECT version_num FROM alembic_version;" | in_snap | q | tr -d '\r')"
largest="$(echo "SELECT coalesce(max(pg_total_relation_size(c.oid)), 0) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'm', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema');" | in_snap | q | tr -d '\r')"
free="$(df -B1 --output=avail "$PGDATA" | tail -n 1 | tr -d ' \r')"
{
  cat "$o/manifest.lines"
  printf 'alembic\t%s\n' "$rev"
  printf 'largest_table_bytes\t%s\n' "$largest"
  printf 'pgdata_free_bytes\t%s\n' "$free"
} >"$o/db.tsv"
rm -f "$o/manifest.lines"
: >"$o/WORK_OK"
WORK

# Container block: private temp directory, snapshot session, tar of the result on stdout.
read -r -d '' CT_DUMP <<'CT' || true
set -eu
umask 077
d="$(mktemp -d)"
trap 'rm -rf "$d"' EXIT
printf '%s' "$WORK_B64" | base64 -d >"$d/work.sh"
printf '%s' "$MANIFEST_B64" | base64 -d >"$d/manifest.sql"
D="$d"; export D TABLES_CSV
PGOPTIONS="-c default_transaction_read_only=on"; export PGOPTIONS
printf '%s\n' \
  "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" \
  "SELECT pg_export_snapshot() AS snap \\gset" \
  "\\setenv SNAP :snap" \
  "\\! sh \"\$D/work.sh\"" \
  "COMMIT;" |
  psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" >&2
[ -f "$d/out/WORK_OK" ] || { echo "extraction did not complete" >&2; exit 3; }
rm -f "$d/out/WORK_OK"
tar -C "$d/out" -cf - rows schema.dump db.tsv
CT

start="$(date +%s)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
tables_csv="$(IFS=,; echo "${TABLES[*]}")"
docker exec -i -e TABLES_CSV="$tables_csv" -e WORK_B64="$(printf '%s\n' "$WORK" | base64 -w0)" \
  -e MANIFEST_B64="$(base64 -w0 <"$ACCOUNTS_SHARE_DIR/accounts-manifest.sql")" \
  "$c" sh -c "$CT_DUMP" </dev/null >"$work/db.tar" || fail "extraction in the db container failed"
mkdir "$work/a"
tar -C "$work/a" -xf "$work/db.tar" || fail "extraction output unreadable"
rm -f "$work/db.tar"
for t in "${TABLES[@]}"; do
  [ -f "$work/a/rows/$t.copy" ] && [ -s "$work/a/rows/$t.columns" ] || fail "rows of $t missing"
done
[ "$(head -c 5 "$work/a/schema.dump")" = PGDMP ] || fail "schema dump is not a custom-format dump"
db="$work/a/db.tsv"
[ "$(grep -c '^table	' "$db")" = "${#TABLES[@]}" ] || fail "manifest incomplete"
rev="$(sed -n 's/^alembic	//p' "$db")"
largest="$(sed -n 's/^largest_table_bytes	//p' "$db")"
free="$(sed -n 's/^pgdata_free_bytes	//p' "$db")"
[ -n "$rev" ] && [[ "$largest" =~ ^[0-9]+$ ]] && [[ "$free" =~ ^[0-9]+$ ]] || fail "manifest facts unreadable"
rows_total="$(awk -F '\t' '$1 == "table" { s += $3 } END { print s + 0 }' "$db")"

# The secrets file goes into the archive as is; only the names of its variables are listed.
mkdir "$work/a/env"
cp "$ACCOUNTS_ENV_FILE" "$work/a/env/datacommune.env"
sha="unknown"
if [ -n "$ACCOUNTS_SHA_FILE" ] && [ -f "$ACCOUNTS_SHA_FILE" ]; then
  sha="$(tr -d ' \r\n' <"$ACCOUNTS_SHA_FILE")"
  [[ "$sha" =~ ^[0-9a-f]{7,40}$ ]] || sha="unreadable"
fi
{
  cat "$db"
  printf 'sha\t%s\n' "$sha"
  printf 'host\t%s\n' "$(hostname)"
  printf 'date\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}\([A-Za-z_][A-Za-z0-9_]*\)=.*/env_name\t\2/p' "$work/a/env/datacommune.env"
} >"$work/a/manifest.tsv"
rm -f "$db"

tar -C "$work/a" -cf - rows schema.dump manifest.tsv env | age -r "$ACCOUNTS_AGE_RECIPIENT" -o "$work/archive.age" ||
  fail "encryption failed"
rm -rf "$work/a"
[ "$(head -c 21 "$work/archive.age")" = "age-encryption.org/v1" ] || fail "encrypted archive has no age header"
bytes="$(wc -c <"$work/archive.age" | tr -d ' ')"
cat "$work/archive.age"
echo "ACCOUNTS_DUMP ok tables=${#TABLES[@]} rows=$rows_total bytes=$bytes seconds=$(( $(date +%s) - start )) pgdata_free_bytes=$free largest_table_bytes=$largest" >&2
