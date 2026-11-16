#!/usr/bin/env bash
# Move the state of the public stack that no other script carries, from the serving host to
# the next one (plan 05-03, bascule of 05-05), and compare both sides. Run on the workstation.
#
#   infra/public/public-migrate.sh survey      --source <s> --target <t>
#   infra/public/public-migrate.sh goatcounter --source <s> --target <t> [--apply]
#   infra/public/public-migrate.sh compare     --source <s> --target <t>
#
#   <s>, <t>: pct:<ctid> (a CT of $PUBLIC_SSH_HOST, through `pct exec`) or ssh:<alias> (a host
#   of ~/.ssh/config, the bought server). The source only ever receives READ scripts: this
#   file has no code path that writes there. Without --apply nothing is written anywhere; the
#   reads still run (survey and fingerprints), so a dry run shows what --apply would do.
#
# What the other scripts already carry (not repeated here):
#   national base      zfs clone/send of $PUBLIC_ZFS_SOURCE@pub-<stamp>, public-db-restore.sh
#   accounts + secrets accounts-backup.sh (hypervisor, private) --restore <archive> --target <t> --apply
#                      (encrypted archive of the nightly backup: the source is not touched)
#   code + images      public-deploy.sh (pct targets; an ssh host needs the adaptation the
#                      migration procedure of 05-03 describes)
#   tunnel route       Cloudflare, gesture of Ianis (no DNS or route is changed by a script)
#
# Steps:
#   survey       both sides, read only: served sha, alembic revision, containers, free space
#                of the release directory, GoatCounter database size and fingerprint. Last
#                line MIGRATE_SURVEY ok|REFUSED.
#   goatcounter  the GoatCounter SQLite database (audience since 2026-11-11; it is in no
#                backup today). Source: python3 sqlite3 opens it read-only (mode=ro, the WAL
#                is read), backs it up into memory and writes the serialized copy to stdout:
#                no file is written on the source (the read updates the -shm wal-index, the
#                shared memory every reader of a WAL database uses, as GoatCounter does;
#                SQLite creates an empty -wal/-shm if they are absent, never the case while
#                GoatCounter runs). The copy is fingerprinted (sha256 of the
#                serialized pages) on both sides with the same method; equal fingerprints ->
#                MIGRATE_GOATCOUNTER unchanged, nothing written (idempotent). With --apply,
#                refused (80) while the goatcounter container of the target runs (it would
#                write over the copy); the copy lands as db.sqlite3.part in the volume
#                <project>_goatcounter_data of the target (created with the compose labels
#                when absent), passes PRAGMA integrity_check, the target -wal/-shm are
#                removed, then it is renamed in place (owner 1000:1000, the image's user).
#                Hits counted on the source after the copy are not carried: replay this step
#                at the switch, after collection is closed (MESURE_AUDIENCE_ACTIVE=non).
#   compare      both sides, read only: served sha, alembic revision, GoatCounter fingerprint
#                and page views (sum of hit_counts), /v1/health statut through each caddy loopback. Last line
#                MIGRATE_COMPARE ok|DIFF.
#
# Settings: PUBLIC_SSH_HOST and PUBLIC_PROJECT from public.local.env (see lib.sh), PUBLIC_DIR
# (release directory, read for current_sha), MIGRATE_CADDY_PORT (loopback port of caddy,
# default 8080). Never a secret: no env file is read, no secret crosses this script.
# Refused (exit 2) before any remote call: source = target, a target that is a protected CT
# (PUBLIC_PROTECTED_CTIDS, the work base PUBLIC_SOURCE_CTID, and the CT that serves today
# MIGRATE_SERVING_CTID, default PUBLIC_CTID), an unknown step or argument.
# Exit: 0 ok | 2 usage or refused before acting | 80 refused on the target, nothing written |
# 81 failed (the target keeps its previous database; a .part may remain and is overwritten
# by the next run) | 3 compare found a difference.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBLIC_SETTINGS_FILE="${PUBLIC_SETTINGS_FILE:-$here/public.local.env}"
# shellcheck source=public.env.example
[ -f "$PUBLIC_SETTINGS_FILE" ] && . "$PUBLIC_SETTINGS_FILE"
: "${PUBLIC_SSH_HOST:?set PUBLIC_SSH_HOST (public.local.env)}"
: "${PUBLIC_PROJECT:?set PUBLIC_PROJECT (public.local.env)}"
: "${PUBLIC_DIR:?set PUBLIC_DIR (public.local.env)}"
: "${PUBLIC_PROTECTED_CTIDS:=}"
: "${PUBLIC_SOURCE_CTID:=}"
: "${MIGRATE_SERVING_CTID:=${PUBLIC_CTID:?set PUBLIC_CTID or MIGRATE_SERVING_CTID (public.local.env)}}"
: "${MIGRATE_CADDY_PORT:=8080}"

log() { printf '%s\n' "$*" >&2; }
usage() { log "usage: $0 survey|goatcounter|compare --source pct:<id>|ssh:<alias> --target pct:<id>|ssh:<alias> [--apply]"; exit 2; }

step="${1:-}"; [ "$#" -gt 0 ] && shift
case "$step" in survey|goatcounter|compare) ;; *) usage ;; esac
source_spec="" target_spec="" apply=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --source) source_spec="${2:-}"; [ "$#" -ge 2 ] || usage; shift 2 ;;
    --target) target_spec="${2:-}"; [ "$#" -ge 2 ] || usage; shift 2 ;;
    --apply) apply=1; shift ;;
    *) log "unknown argument $1"; usage ;;
  esac
done
[ "$apply" = 0 ] || [ "$step" = goatcounter ] || { log "--apply only goes with goatcounter"; exit 2; }

spec_ok() {
  case "$1" in
    pct:*) [[ "${1#pct:}" =~ ^[0-9]+$ ]] ;;
    ssh:*) [[ "${1#ssh:}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ;;
    *) return 1 ;;
  esac
}
spec_ok "$source_spec" || { log "--source: pct:<numeric id> or ssh:<alias>, got '$source_spec'"; exit 2; }
spec_ok "$target_spec" || { log "--target: pct:<numeric id> or ssh:<alias>, got '$target_spec'"; exit 2; }
[ "$source_spec" != "$target_spec" ] || { log "source and target are the same ($source_spec)"; exit 2; }
if [[ "$target_spec" == pct:* ]]; then
  for protected in $MIGRATE_SERVING_CTID $PUBLIC_SOURCE_CTID $PUBLIC_PROTECTED_CTIDS; do
    [ "${target_spec#pct:}" != "$protected" ] || { log "target ct/$protected is protected: never written by a migration"; exit 2; }
  done
fi

# Python run on a side: fingerprint (or the bytes) of a consistent read-only copy.
read -r -d '' PY_SNAPSHOT <<'PY' || true
import hashlib, sqlite3, sys
path, mode = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
mem = sqlite3.connect(":memory:")
src.backup(mem)
src.close()
data = mem.serialize()
if mode == "bytes":
    sys.stdout.buffer.write(data)
else:
    # GoatCounter folds raw hits into hourly hit_counts and prunes them: page views = sum.
    has = mem.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hit_counts'").fetchone()
    views = (mem.execute("SELECT coalesce(sum(total), 0) FROM hit_counts").fetchone()[0]
             if has else -1)
    print(f"sha256={hashlib.sha256(data).hexdigest()} bytes={len(data)} views={views}")
PY

# Shared part of every side script: where the GoatCounter database is (read only).
read -r -d '' SIDE_PRELUDE <<'SH' || true
vol="${PROJECT}_goatcounter_data"
gc_dir="$(docker volume inspect -f '{{.Mountpoint}}' "$vol" 2>/dev/null || true)"
gc_db="${gc_dir:+$gc_dir/db.sqlite3}"
gc_running="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=goatcounter)"
fingerprint() {
  if [ -n "$gc_db" ] && [ -f "$gc_db" ]; then python3 -c "$PY_SNAPSHOT" "$gc_db" fingerprint
  else echo "sha256=none bytes=0 views=-1"; fi
}
SH

# Read-only facts of one side, one "key value" per line.
read -r -d '' FACTS_SCRIPT <<'SH' || true
echo "sha=$(cat "$PUBLIC_DIR/current_sha" 2>/dev/null || echo none)"
db="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=db)"
if [ -n "$db" ]; then
  echo "alembic=$(docker exec "$db" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"' 2>/dev/null || echo unreadable)"
else echo "alembic=no-db-container"; fi
echo "containers=$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Label "com.docker.compose.service"}}' | sort | paste -sd, -)"
echo "release_dir_free_mb=$(df -Pm "$PUBLIC_DIR" 2>/dev/null | awk 'NR==2 {print $4}')"
echo "goatcounter_running=$([ -n "$gc_running" ] && echo yes || echo no)"
echo "goatcounter $(fingerprint)"
statut="$(curl -fsS -m 10 -H 'Host: api.datacommune.fr' "http://127.0.0.1:$CADDY_PORT/v1/health" 2>/dev/null |
  python3 -c 'import json,sys; print(json.load(sys.stdin).get("statut","?"))' 2>/dev/null || echo unreachable)"
echo "health=$statut"
SH

# Write side of the copy (target only): the serialized database arrives on stdin.
read -r -d '' WRITE_SCRIPT <<'SH' || true
[ -z "$gc_running" ] || { echo "goatcounter runs on the target: stop it first (it would write over the copy)" >&2; exit 80; }
if [ -z "$gc_dir" ]; then
  docker volume create --label "com.docker.compose.project=$PROJECT" --label com.docker.compose.volume=goatcounter_data "$vol" >/dev/null
  gc_dir="$(docker volume inspect -f '{{.Mountpoint}}' "$vol")"; gc_db="$gc_dir/db.sqlite3"
fi
part="$gc_dir/db.sqlite3.part"
cat > "$part" || exit 81
[ -s "$part" ] || { echo "empty copy" >&2; rm -f "$part"; exit 81; }
python3 -c 'import sqlite3,sys; sys.exit(0 if sqlite3.connect(sys.argv[1]).execute("PRAGMA integrity_check").fetchone()[0] == "ok" else 1)' "$part" ||
  { echo "integrity_check failed" >&2; exit 81; }
rm -f "$gc_db-wal" "$gc_db-shm"
chown 1000:1000 "$part"; chmod 644 "$part"
mv -f "$part" "$gc_db"
echo "written $gc_db" >&2
SH

# remote <spec> <script>: run a bash script on one side. The script travels base64-encoded on
# the command line (as in lib.sh), so the standard input of the side stays free for data.
remote() {
  local spec="$1" script payload inner
  script="set -euo pipefail
$(printf 'export PROJECT=%q CADDY_PORT=%q PUBLIC_DIR=%q\n' "$PUBLIC_PROJECT" "$MIGRATE_CADDY_PORT" "$PUBLIC_DIR")
$(printf 'PY_SNAPSHOT=%q' "$PY_SNAPSHOT")
$SIDE_PRELUDE
$2"
  payload="$(printf '%s\n' "$script" | base64 -w0)"
  inner="bash -c 'eval \"\$(printf %s $payload | base64 -d)\"'"
  case "$spec" in
    pct:*) ssh -o BatchMode=yes "$PUBLIC_SSH_HOST" "pct exec ${spec#pct:} -- $inner" ;;
    ssh:*) ssh -o BatchMode=yes "${spec#ssh:}" "$inner" ;;
  esac
}

facts() { remote "$1" "$FACTS_SCRIPT" </dev/null; }
value() { printf '%s\n' "$1" | sed -n "s/^$2[= ]//p"; }

case "$step" in
  survey)
    s="$(facts "$source_spec")" || { echo "MIGRATE_SURVEY REFUSED source unreadable"; exit 80; }
    printf '%s\n' "$s" | sed 's/^/source /'
    t="$(facts "$target_spec")" || { echo "MIGRATE_SURVEY REFUSED target unreadable"; exit 80; }
    printf '%s\n' "$t" | sed 's/^/target /'
    echo "MIGRATE_SURVEY ok"
    ;;

  compare)
    s="$(facts "$source_spec")" || { echo "MIGRATE_COMPARE DIFF source unreadable"; exit 3; }
    t="$(facts "$target_spec")" || { echo "MIGRATE_COMPARE DIFF target unreadable"; exit 3; }
    diff=0
    for key in sha alembic goatcounter health; do
      sv="$(value "$s" "$key")"; tv="$(value "$t" "$key")"
      if [ "$sv" = "$tv" ]; then echo "same $key $sv"; else echo "DIFF $key source=$sv target=$tv"; diff=1; fi
    done
    if [ "$diff" = 0 ]; then echo "MIGRATE_COMPARE ok"; exit 0; fi
    echo "MIGRATE_COMPARE DIFF"; exit 3
    ;;

  goatcounter)
    src_fp="$(remote "$source_spec" '[ -n "$gc_db" ] && [ -f "$gc_db" ] || { echo "no goatcounter database on the source" >&2; exit 80; }
fingerprint' </dev/null)" || { echo "MIGRATE_GOATCOUNTER REFUSED source"; exit 80; }
    dst_fp="$(remote "$target_spec" fingerprint </dev/null)" || { echo "MIGRATE_GOATCOUNTER REFUSED target unreadable"; exit 80; }
    log "source: $src_fp"; log "target: $dst_fp"
    if [ "${src_fp%% *}" = "${dst_fp%% *}" ]; then echo "MIGRATE_GOATCOUNTER unchanged ${src_fp%% *}"; exit 0; fi
    if [ "$apply" = 0 ]; then
      echo "+ copy $source_spec -> $target_spec (volume ${PUBLIC_PROJECT}_goatcounter_data, ${src_fp#* })"
      echo "MIGRATE_GOATCOUNTER DRY_RUN"; exit 0
    fi
    # Read-only snapshot on the source, piped to the write script of the target only.
    set +e
    remote "$source_spec" 'python3 -c "$PY_SNAPSHOT" "$gc_db" bytes' </dev/null | remote "$target_spec" "$WRITE_SCRIPT"
    rc=("${PIPESTATUS[@]}"); set -e
    # The target's verdict first: a target that refuses closes the pipe, so the source
    # reader then fails on a broken pipe, which is not a source fault.
    case "${rc[1]}" in
      0) ;;
      80) echo "MIGRATE_GOATCOUNTER REFUSED target"; exit 80 ;;
      *) echo "MIGRATE_GOATCOUNTER FAILED target (previous database kept)"; exit 81 ;;
    esac
    [ "${rc[0]}" = 0 ] || { echo "MIGRATE_GOATCOUNTER FAILED source read"; exit 81; }
    new_fp="$(remote "$target_spec" fingerprint </dev/null)"
    if [ "${new_fp%% *}" = "${src_fp%% *}" ]; then echo "MIGRATE_GOATCOUNTER copied $new_fp"; exit 0; fi
    # The source counted hits during the copy: the copy is a later state than src_fp.
    echo "MIGRATE_GOATCOUNTER copied $new_fp (the source moved since its fingerprint: replay after closing collection)"
    ;;
esac
