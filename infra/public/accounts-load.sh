#!/usr/bin/env bash
# Reload an accounts archive made by accounts-dump.sh (plan 05-01, D-07). Installed as
# /usr/local/sbin/dc-accounts-load on the production host and in the work base container (daily check).
# Reads the DECRYPTED archive (tar) on stdin: the hypervisor decrypts with `age -d` and pipes it here,
# no clear file is written outside the private temp directory of this script.
#
#   dc-accounts-load --verify [--dry-run]
#       throw-away database dc_accounts_verify of the local cluster: DROP IF EXISTS, CREATE
#       from template0, pg_restore --schema-only of schema.dump, rows staged in
#       public_reset_rows, public-reset.sql (one transaction, row by row comparison with the
#       archive), then accounts-manifest.sql recomputed and compared with the manifest of the
#       archive (= the production rows at extraction); the database is dropped, also on failure
#   dc-accounts-load --apply [--drop-roles <r1,r2>] [--apply-env <NAMES>] [--dry-run]
#       the same reload into the SERVED database; refused (exit 2) when its alembic revision
#       differs from the archive's. --drop-roles: roles of a work cluster to remove (05-03)
#   dc-accounts-load --apply-env <NAME1,NAME2> [--dry-run]
#       only the named variables of the archived secrets file are merged into the secrets
#       file of this host: one line per name, the other lines kept, mode 600, atomic rename;
#       refused when a name is not in the archive. Values are never printed.
#
# Settings: /etc/datacommune/accounts-backup.env (DC_ACCOUNTS_CONFIG overrides the path),
# plain KEY=value lines: ACCOUNTS_PROJECT, ACCOUNTS_ENV_FILE, ACCOUNTS_SHARE_DIR (holds
# accounts-manifest.sql and public-reset.sql, played as they are).
# public-reset.sql inputs: tables and counts of the manifest; drop_roles = --drop-roles (empty
# by default); keep_roles = the roles present in the target minus drop_roles, so its role
# check (made for the switch) only proves the requested roles are gone.
# No DSN and no password is read: psql and pg_restore run in the db container of
# ACCOUNTS_PROJECT on its local socket with its own environment.
# Last stdout line:
#   ACCOUNTS_VERIFY ok|FAILED tables=<n> rows=<n> env_names=<n> seconds=<n>
#   ACCOUNTS_RESTORE_OK tables=<n> rows=<n> api_keys=<n> seconds=<n> | ACCOUNTS_RESTORE REFUSED|FAILED
#   ACCOUNTS_ENV_OK names=<names> | ACCOUNTS_ENV REFUSED|FAILED
# Exit 0, 2 refused or usage, 1 failed.
set -euo pipefail
umask 077

readonly VERIFY_DB=dc_accounts_verify
start="$(date +%s)"

mode="" drop_roles="" env_names="" dry=0
usage() { echo "usage: dc-accounts-load --verify | --apply [--drop-roles r1,r2] [--apply-env N1,N2] | --apply-env N1,N2 [--dry-run]" >&2; echo "ACCOUNTS_LOAD REFUSED"; exit 2; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --verify) [ -z "$mode" ] || usage; mode=verify; shift ;;
    --apply) [ -z "$mode" ] || usage; mode=apply; shift ;;
    --drop-roles) drop_roles="${2:-}"; shift 2 || usage ;;
    --apply-env) env_names="${2:-}"; shift 2 || usage ;;
    --dry-run) dry=1; shift ;;
    *) usage ;;
  esac
done
[ -n "$mode" ] || { [ -n "$env_names" ] && mode=env; } || usage
[ "$mode" != verify ] || { [ -z "$env_names" ] && [ -z "$drop_roles" ]; } || usage
[ "$mode" = apply ] || [ -z "$drop_roles" ] || usage
ident_list() { [[ "$1" =~ ^[a-z_][a-z0-9_]*(,[a-z_][a-z0-9_]*)*$ ]]; }
[ -z "$drop_roles" ] || ident_list "$drop_roles" || usage
[ -z "$env_names" ] || [[ "$env_names" =~ ^[A-Za-z_][A-Za-z0-9_]*(,[A-Za-z_][A-Za-z0-9_]*)*$ ]] || usage

case "$mode" in verify) label=ACCOUNTS_VERIFY ;; apply) label=ACCOUNTS_RESTORE ;; env) label=ACCOUNTS_ENV ;; esac
refuse() { echo "$1" >&2; echo "$label REFUSED"; exit 2; }

config="${DC_ACCOUNTS_CONFIG:-/etc/datacommune/accounts-backup.env}"
[ -f "$config" ] || refuse "settings file $config absent"
ACCOUNTS_PROJECT="" ACCOUNTS_ENV_FILE="" ACCOUNTS_SHARE_DIR=""
while IFS='=' read -r key value; do
  value="${value%$'\r'}"
  case "$key" in
    ACCOUNTS_PROJECT|ACCOUNTS_ENV_FILE|ACCOUNTS_SHARE_DIR) printf -v "$key" '%s' "$value" ;;
  esac
done <"$config"
plain_path() { [[ "$1" =~ ^/[A-Za-z0-9/._-]+$ ]] && [[ "$1" != *..* ]]; }
[[ "$ACCOUNTS_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || refuse "ACCOUNTS_PROJECT missing or odd"
plain_path "$ACCOUNTS_SHARE_DIR" || refuse "ACCOUNTS_SHARE_DIR is not a plain absolute path"
for f in accounts-manifest.sql public-reset.sql; do
  [ -f "$ACCOUNTS_SHARE_DIR/$f" ] || refuse "$ACCOUNTS_SHARE_DIR/$f absent"
done
[ -z "$env_names" ] || plain_path "$ACCOUNTS_ENV_FILE" || refuse "ACCOUNTS_ENV_FILE is not a plain absolute path"

# The archive, unpacked in a private directory (removed on exit).
work="$(mktemp -d)"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT
mkdir "$work/a"
tar -C "$work/a" -xf - || refuse "stdin is not a readable archive"
A="$work/a"
[ -f "$A/manifest.tsv" ] && [ -f "$A/schema.dump" ] || refuse "archive without manifest.tsv or schema.dump"
TABLES=()
while IFS=$'\t' read -r kind t n _h cols; do
  [ "$kind" = table ] || continue
  [[ "$t" =~ ^[a-z_][a-z0-9_]*$ ]] && [[ "$n" =~ ^[0-9]+$ ]] || refuse "manifest line of table '$t' is malformed"
  [[ "$cols" =~ ^\"?[a-z_][a-z0-9_]*\"?(,\"?[a-z_][a-z0-9_]*\"?)*$ ]] || refuse "column list of $t is malformed"
  [ -f "$A/rows/$t.copy" ] || refuse "rows of $t missing from the archive"
  TABLES+=("$t")
done <"$A/manifest.tsv"
[ "${#TABLES[@]}" -gt 0 ] || refuse "manifest lists no table"
field() { awk -F '\t' -v t="$1" -v k="$2" '$1 == "table" && $2 == t { print $k }' "$A/manifest.tsv"; }
rev="$(sed -n 's/^alembic	//p' "$A/manifest.tsv")"
rows_total="$(awk -F '\t' '$1 == "table" { s += $3 } END { print s + 0 }' "$A/manifest.tsv")"
names_total="$(grep -c '^env_name	' "$A/manifest.tsv" || true)"
tables_csv="$(IFS=,; echo "${TABLES[*]}")"

# --apply-env: merge the named variables only. Values stay in files, never on a terminal.
apply_env() {
  local src="$A/env/datacommune.env" n dir tmp target="$ACCOUNTS_ENV_FILE"
  [ -f "$src" ] || { echo "archive holds no secrets file" >&2; echo "ACCOUNTS_ENV REFUSED"; exit 2; }
  for n in ${env_names//,/ }; do
    grep -Eq "^[[:space:]]*(export[[:space:]]+)?$n=" "$src" ||
      { echo "$n is not in the archived secrets file" >&2; echo "ACCOUNTS_ENV REFUSED"; exit 2; }
  done
  if [ "$dry" = 1 ]; then echo "+ merge into $target: ${env_names}"; echo "ACCOUNTS_ENV dry-run names=$env_names"; return 0; fi
  dir="$(dirname "$target")"
  [ -d "$dir" ] || { echo "$dir does not exist" >&2; echo "ACCOUNTS_ENV FAILED"; exit 1; }
  tmp="$(mktemp "$dir/.accounts-env.XXXXXX")"
  # shellcheck disable=SC2064 # the name is fixed now
  trap "rm -f '$tmp'; cleanup" EXIT
  chmod 600 "$tmp"
  local current=/dev/null
  [ ! -f "$target" ] || current="$target"
  awk -v names="$env_names" -v src="$src" '
    function name_of(s) { sub(/^[ \t]*(export[ \t]+)?/, "", s); if (s !~ /^[A-Za-z_][A-Za-z0-9_]*=/) return ""; sub(/=.*/, "", s); return s }
    function body_of(s) { sub(/^[ \t]*(export[ \t]+)?/, "", s); return s }
    BEGIN {
      n = split(names, list, ",")
      for (i = 1; i <= n; i++) want[list[i]] = 1
      while ((getline line < src) > 0) { k = name_of(line); if (k in want) value[k] = body_of(line) }
    }
    { k = name_of($0); if (k != "" && (k in want)) next; print }
    END { for (i = 1; i <= n; i++) print value[list[i]] }
  ' "$current" >"$tmp" || { echo "merge failed" >&2; echo "ACCOUNTS_ENV FAILED"; exit 1; }
  mv -f "$tmp" "$target"
  trap cleanup EXIT
  echo "ACCOUNTS_ENV_OK names=$env_names"
}

if [ "$mode" = env ]; then apply_env; exit 0; fi

c="$(docker ps -q --filter "label=com.docker.compose.project=$ACCOUNTS_PROJECT" \
  --filter label=com.docker.compose.service=db | head -n 1)"
[ -n "$c" ] || refuse "no running db container in project $ACCOUNTS_PROJECT"
# db_psql <database|""> [<sql>]: psql in the db container; SQL on stdin, or <sql> with COPY
# data on stdin. The values travel as environment variables, never re-parsed by a shell.
IN_DB='if [ -n "$SQL" ]; then exec psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "${DB:-$POSTGRES_DB}" -c "$SQL"; else exec psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "${DB:-$POSTGRES_DB}"; fi'
db_psql() { docker exec -i -e DB="$1" -e SQL="${2:-}" "$c" sh -c "$IN_DB"; }

if [ "$mode" = verify ]; then
  db="$VERIFY_DB"
else
  db=""
  target_rev="$(db_psql "" "SELECT version_num FROM alembic_version" </dev/null | tr -d '\r ')" ||
    refuse "alembic revision of the target unreadable"
  [ -n "$rev" ] && [ "$target_rev" = "$rev" ] ||
    refuse "archive revision '$rev' differs from the target revision '$target_rev': migrate first"
fi

if [ "$dry" = 1 ]; then
  [ "$mode" = apply ] || printf '+ DROP DATABASE IF EXISTS %s; CREATE DATABASE %s TEMPLATE template0; pg_restore --schema-only\n' "$db" "$db"
  for t in "${TABLES[@]}"; do printf '+ stage %s (%s rows)\n' "$t" "$(field "$t" 3)"; done
  printf '+ public-reset.sql tables=%s drop_roles=%s\n' "$tables_csv" "${drop_roles:-none}"
  if [ "$mode" = verify ]; then
    echo "ACCOUNTS_VERIFY dry-run tables=${#TABLES[@]} rows=$rows_total env_names=$names_total"
  else
    [ -z "$env_names" ] || apply_env
    echo "ACCOUNTS_RESTORE dry-run tables=${#TABLES[@]} rows=$rows_total"
  fi
  exit 0
fi

failed() { echo "$1" >&2
  if [ "$mode" = verify ]; then
    echo "ACCOUNTS_VERIFY FAILED tables=${#TABLES[@]} rows=$rows_total env_names=$names_total seconds=$(( $(date +%s) - start ))"
  else echo "ACCOUNTS_RESTORE FAILED"; fi
  exit 1
}
if [ "$mode" = verify ]; then
  drop_verify() { db_psql postgres "DROP DATABASE IF EXISTS $VERIFY_DB WITH (FORCE)" </dev/null >/dev/null 2>&1 || true; }
  trap 'drop_verify; cleanup' EXIT
  db_psql postgres "DROP DATABASE IF EXISTS $VERIFY_DB WITH (FORCE)" </dev/null >/dev/null || failed "verify database not dropped"
  db_psql postgres "CREATE DATABASE $VERIFY_DB TEMPLATE template0" </dev/null >/dev/null || failed "verify database not created"
  docker exec -i -e DB="$VERIFY_DB" "$c" sh -c \
    'exec pg_restore --schema-only --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" -d "$DB"' \
    <"$A/schema.dump" >&2 || failed "pg_restore of the schema failed"
fi

# Rows staged as public-db-restore.sh stages them, then public-reset.sql as it is.
db_psql "$db" "DROP SCHEMA IF EXISTS public_reset_rows CASCADE; CREATE SCHEMA public_reset_rows" </dev/null >/dev/null ||
  failed "staging schema not created"
counts=""
for t in "${TABLES[@]}"; do
  cols="$(field "$t" 5)"
  db_psql "$db" "CREATE TABLE public_reset_rows.$t AS SELECT $cols FROM public.$t WITH NO DATA" </dev/null >/dev/null ||
    failed "staging table of $t not created"
  db_psql "$db" "COPY public_reset_rows.$t ($cols) FROM STDIN" <"$A/rows/$t.copy" >/dev/null ||
    failed "rows of $t not staged"
  counts="$counts${counts:+,}$t=$(field "$t" 3)"
done
present="$(db_psql "$db" "SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%' ORDER BY 1" </dev/null | tr -d '\r')" ||
  failed "roles of the target unreadable"
keep=""
for r in $present; do
  case ",$drop_roles," in *",$r,"*) ;; *) keep="$keep${keep:+,}$r" ;; esac
done
out="$( { printf "SET public_reset.tables = '%s';\n" "$tables_csv"
          printf "SET public_reset.counts = '%s';\n" "$counts"
          printf "SET public_reset.drop_roles = '%s';\n" "$drop_roles"
          printf "SET public_reset.keep_roles = '%s';\n" "$keep"
          cat "$ACCOUNTS_SHARE_DIR/public-reset.sql"; } | db_psql "$db" | tr -d '\r')" ||
  failed "public-reset.sql failed: rows not reloaded identically"
want_keys="$(field api_keys 3)"
api_keys="$(printf '%s\n' "$out" | sed -n 's/^PUBLIC_RESET_OK api_keys=\([0-9]*\)$/\1/p' | tail -n 1)"
[ -n "$api_keys" ] || failed "public-reset.sql did not report PUBLIC_RESET_OK"
[ -z "$want_keys" ] || [ "$api_keys" = "$want_keys" ] || failed "api_keys: $api_keys reloaded, $want_keys in the archive"

if [ "$mode" = verify ]; then
  # Same manifest as at extraction: the reloaded rows are the production rows of that time.
  { printf "SET accounts_manifest.tables = '%s';\n" "$tables_csv"; cat "$ACCOUNTS_SHARE_DIR/accounts-manifest.sql"; } |
    db_psql "$VERIFY_DB" | tr -d '\r' >"$work/manifest.after" || failed "manifest of the reloaded rows failed"
  grep '^table	' "$A/manifest.tsv" >"$work/manifest.before" || true
  if ! cmp -s "$work/manifest.before" "$work/manifest.after"; then
    for t in "${TABLES[@]}"; do
      [ "$(awk -F '\t' -v t="$t" '$2 == t' "$work/manifest.before")" = \
        "$(awk -F '\t' -v t="$t" '$2 == t' "$work/manifest.after")" ] || echo "MISMATCH $t" >&2
    done
    failed "reloaded rows differ from the manifest of the archive"
  fi
  db_psql postgres "DROP DATABASE IF EXISTS $VERIFY_DB WITH (FORCE)" </dev/null >/dev/null || failed "verify database not dropped"
  trap cleanup EXIT
  echo "ACCOUNTS_VERIFY ok tables=${#TABLES[@]} rows=$rows_total env_names=$names_total seconds=$(( $(date +%s) - start ))"
  exit 0
fi

echo "ACCOUNTS_RESTORE_OK tables=${#TABLES[@]} rows=$rows_total api_keys=$api_keys seconds=$(( $(date +%s) - start ))"
[ -z "$env_names" ] || apply_env
