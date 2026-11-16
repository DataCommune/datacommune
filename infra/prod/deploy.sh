#!/usr/bin/env bash
# Build and deploy a commit on the rented production server, or roll back to the previous
# release. Same model and same guards as infra/public/public-deploy.sh, without hypervisor/pct.
#
#   infra/prod/deploy.sh [--dry-run] [--no-tunnel] [<commit>]     # default: HEAD
#   infra/prod/deploy.sh [--dry-run] [--no-tunnel] --rollback
#   infra/prod/deploy.sh --check-env-only <env file> [--remediate]  # local, no connection
#   --allow-unpublished (deploy): deploy a commit that is not on the public master (emergency
#     only; printed as a WARNING). Without it, a commit is deployed only when it is an ancestor
#     of $PROD_PUBLISHED_REF (default refs/remotes/public/master, D-135: nothing is served that
#     is not on the public master): refused (80) otherwise, or when that ref is unknown
#     (`git fetch public` first). --rollback goes back to a release already served.
#
# Run on the workstation (Git Bash). Settings: infra/prod/prod.env (prod.env.example).
# The code reaches the server as `git archive <sha> api frontend` sent by scp (no GitHub
# credential on the server), extracted in $PROD_DIR/releases/<sha12> (+ .complete); every
# compose command runs from inside the release (compose 5 refuses a build context outside
# the working directory) with -f api/docker-compose.public.yml -f api/docker-compose.prod.yml.
#
# Deploy, on the server, in this order (deploy and rollback hold $PROD_DIR/.deploy.lock: a
# second run while one is going is refused, 80):
#   1. $PROD_ENV_FILE: present, readable, mode 600 (a drifted mode is set back to 600 on the
#      existing file, values never rewritten), POSTGRES_PASSWORD, JWT_SECRET_KEY,
#      API_KEY_HMAC_SECRET, ADMIN_PASSWORD and CF_TUNNEL_TOKEN set once each.
#      PUBLIC_DC_API_KEY absent = first deployment (bootstrap below); present = it must be a
#      real dc_anon_* key (the bootstrap placeholder is refused). Names only, values never printed;
#   2. build (stack still running). First deployment: api only, with placeholder values for
#      PUBLIC_DC_API_KEY / CF_TUNNEL_TOKEN scoped to the api build, db start, migration and key
#      creation; the frontends and the tunnel are never built nor started with them;
#   3. the UID of the api image (`id -u`) must own $PROD_DIR/sources (1000), the sentinel
#      `datacommune-source-archive v1` is written if missing, /sources must be writable by
#      that UID -- before anything starts;
#   4. `up -d db`, wait healthy; an empty base (no table, no alembic_version) is accepted only
#      at the first deployment; tables without alembic_version are refused (base not restored);
#   5. refused (80) while a one-off api container runs (an import) or when the backups
#      filesystem has less free space than the base; pg_dump -Fc to a .partial file, read back
#      whole by pg_restore, then named $PROD_DIR/backups/pre-<sha12>-*.pgdump; the api service
#      of the old release is stopped (checked: no api service container left running), then
#      `alembic upgrade head` in a one-off api container (single transaction: on failure the
#      schema is unchanged and the old api is started again);
#   6. first deployment only: the existing CLI scripts.rotate_anon_key creates the real
#      dc_anon_* key in the base; its output goes to a 0600 temporary file, the single key line
#      is appended to $PROD_ENV_FILE (umask 077, never twice), the file is shredded; then both
#      frontends are built with that key, read by compose from the private file;
#   7. `up -d --no-build`, wait api, frontend, frontend-prod healthy;
#   8. smoke through the only published port, caddy on 127.0.0.1:8080, with Host headers:
#      api-prod and api health, deployed sha on prod. and on the apex, HSTS, API noindex;
#   9. state: current_sha / previous_sha; old images and releases pruned (current + previous
#      kept); pre-deploy dumps pruned to the newest 3, only here, once the release is healthy.
# The schema is never downgraded. A release is started again (automatic return after an
# unhealthy switch, --rollback) only if the revision of the base is the head of its own
# migrations; otherwise nothing is started (87, or 80 for --rollback) and the way back is the
# pre-deploy dump pre-<sha12>-*.pgdump (infra/prod/README.md, "Retour arrière").
#
# Exit codes: 0 deployed | 2 usage | 80 refused, nothing changed | 81 failed before the
# switch (build, db, dump, api stop, migration, key): schema unchanged or first deployment,
# the old api serves again if there was one | 84 migration failed (schema unchanged) and the
# old api does not come back | 86 new release unhealthy and no previous release | 87 new
# release unhealthy, schema migrated beyond the previous release: nothing restarted, restore
# the pre-deploy dump | 90 new release unhealthy, previous restarted and healthy |
# 91 rollback failed (diagnose: `docker compose -p datacommune-prod ps` on the server).
# The server block prints PROD_EXIT=<code>; an ssh failure without that line is a transport failure.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

# Environment preconditions, run as-is on the server (deploy, rollback) and locally by
# --check-env-only. env_check <file> <remediate 0|1>: sets BOOTSTRAP=1 when PUBLIC_DC_API_KEY
# is absent; prints names and counts only, never a value; exits 80 on refusal.
ENV_CHECK_FN='
env_count() { grep -c "^$1=" "$ENV_CHECK_FILE" || true; }
env_set() { [ -n "$(grep -m1 "^$1=" "$ENV_CHECK_FILE" | cut -d= -f2-)" ]; }
env_check() {
  ENV_CHECK_FILE="$1"
  local remediate="$2" mode name n
  [ -f "$ENV_CHECK_FILE" ] || { echo "REFUSED: $ENV_CHECK_FILE missing (private bootstrap block, infra/prod/README.md)" >&2; return 80; }
  [ -r "$ENV_CHECK_FILE" ] || { echo "REFUSED: $ENV_CHECK_FILE not readable by $(id -un)" >&2; return 80; }
  mode="$(stat -c %a "$ENV_CHECK_FILE")"
  if [ "$mode" != 600 ]; then
    if [ "$remediate" = 1 ]; then
      chmod 600 "$ENV_CHECK_FILE"
      echo "REMEDIATED: $ENV_CHECK_FILE mode $mode -> $(stat -c %a "$ENV_CHECK_FILE") (values untouched)" >&2
      [ "$(stat -c %a "$ENV_CHECK_FILE")" = 600 ] || { echo "REFUSED: $ENV_CHECK_FILE still not mode 600" >&2; return 80; }
    else
      echo "REFUSED: $ENV_CHECK_FILE mode $mode, must be 600 (fix: chmod 600 on the existing file, never rewrite it)" >&2
      return 80
    fi
  fi
  for name in POSTGRES_PASSWORD JWT_SECRET_KEY API_KEY_HMAC_SECRET ADMIN_PASSWORD CF_TUNNEL_TOKEN; do
    n="$(env_count "$name")"
    [ "$n" = 1 ] || { echo "REFUSED: $name defined $n time(s) in $ENV_CHECK_FILE, expected once (bootstrap block, README)" >&2; return 80; }
    env_set "$name" || { echo "REFUSED: $name empty in $ENV_CHECK_FILE" >&2; return 80; }
  done
  n="$(env_count PUBLIC_DC_API_KEY)"
  case "$n" in
    0) BOOTSTRAP=1; echo "ENV_OK first deployment: PUBLIC_DC_API_KEY absent, the real key will be created by the CLI" >&2 ;;
    1)
      BOOTSTRAP=0
      case "$(grep -m1 "^PUBLIC_DC_API_KEY=" "$ENV_CHECK_FILE" | cut -d= -f2-)" in
        dc_anon_bootstrap) echo "REFUSED: PUBLIC_DC_API_KEY holds the bootstrap placeholder, not a real key" >&2; return 80 ;;
        dc_anon_?*) echo "ENV_OK PUBLIC_DC_API_KEY present (dc_anon_*)" >&2 ;;
        *) echo "REFUSED: PUBLIC_DC_API_KEY present but not a dc_anon_* key (rotation: infra/public/README.md § 4)" >&2; return 80 ;;
      esac ;;
    *) echo "REFUSED: PUBLIC_DC_API_KEY defined $n times in $ENV_CHECK_FILE" >&2; return 80 ;;
  esac
  return 0
}
'

mode=deploy rev=HEAD no_tunnel=0 dry=0 remediate=0 check_file="" allow_unpublished=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --allow-unpublished) allow_unpublished=1; shift ;;
    --rollback) mode=rollback; shift ;;
    --no-tunnel) no_tunnel=1; shift ;;
    --check-env-only) mode=checkenv; check_file="${2:-}"; [ -n "$check_file" ] || { echo "--check-env-only needs a file" >&2; exit 2; }; shift 2 ;;
    --remediate) remediate=1; shift ;;
    -h|--help) sed -n '2,62p' "$0"; exit 0 ;;
    -*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) rev="$1"; shift ;;
  esac
done

if [ "$mode" = checkenv ]; then
  # Same block as on the server, run here: no connection, no settings file needed.
  eval "$ENV_CHECK_FN"
  rc=0; env_check "$check_file" "$remediate" || rc=$?
  echo "CHECK_ENV_EXIT=$rc"
  exit "$rc"
fi
[ "$remediate" = 0 ] || { echo "--remediate goes with --check-env-only (a deploy always remediates the mode)" >&2; exit 2; }

# shellcheck source=lib.sh
. "$here/lib.sh"
DRY_RUN="$dry"

sha="" tag=""
if [ "$mode" != rollback ]; then
  repo_root="$(git -C "$here" rev-parse --show-toplevel)"
  sha="$(git -C "$repo_root" rev-parse --verify --quiet "$rev^{commit}")" ||
    { log "unknown commit: $rev"; exit 2; }
  tag="${sha:0:12}"
  published_ref="${PROD_PUBLISHED_REF:-refs/remotes/public/master}"
  if [ "$allow_unpublished" = 1 ]; then
    git -C "$repo_root" merge-base --is-ancestor "$sha" "$published_ref" 2>/dev/null ||
      log "WARNING: $sha is not on $published_ref (--allow-unpublished)"
  elif ! git -C "$repo_root" rev-parse --verify --quiet "$published_ref^{commit}" >/dev/null; then
    log "REFUSED: $published_ref is unknown: fetch the public repository (git fetch public), or --allow-unpublished"
    exit 80
  elif ! git -C "$repo_root" merge-base --is-ancestor "$sha" "$published_ref"; then
    log "REFUSED: $sha is not on $published_ref (D-135); publish it first, or --allow-unpublished"
    exit 80
  fi
  target="$(ssh_target)"
  archive="$(mktemp -d)/release-$tag.tgz"
  remote_tgz="/tmp/dc-prod-$tag.tgz"
  log "release: $sha -> $PROD_DIR/releases/$tag on $target ($mode)"
  local_run git -C "$repo_root" archive --format=tar.gz -o "$archive" "$sha" api frontend
  local_run scp -q -o BatchMode=yes "$archive" "$target:$remote_tgz"
  rm -f "$archive"; rmdir "$(dirname "$archive")" 2>/dev/null || true
fi

# Server block: evaluated by bash on the server after ENV_CHECK_FN (read, never expanded here).
IFS= read -r -d '' SERVER_BODY <<'IN_SERVER' || true
keyout=""
cleanup() { [ -z "$keyout" ] || shred -u "$keyout" 2>/dev/null || rm -f "$keyout"; }
state_exit() { echo "PROD_EXIT=$1"; }
fail() { cleanup; echo "$2" >&2; trap - EXIT; state_exit "$1"; exit "$1"; }
trap 'rc=$?; cleanup; [ "$rc" -eq 0 ] || state_exit "$rc"' EXIT

# One deploy or rollback at a time: a run started again after a dropped connection would race
# the first one (two anonymous keys, two migrations). Released when this process ends.
exec 9>"$DIR/.deploy.lock"
flock -n 9 || fail 80 "REFUSED: another deploy or rollback is running on this server ($DIR/.deploy.lock); nothing changed"

SOURCES="$DIR/sources"
SENTINEL="$SOURCES/.datacommune-source-archive"
SENTINEL_CONTENT="datacommune-source-archive v1"
install -d -m 0700 "$DIR/releases" "$DIR/backups"
BOOTSTRAP=0
env_check "$ENV_FILE" 1 || fail 80 "REFUSED: environment preconditions (above)"

old_sha=""
[ -f "$DIR/current_sha" ] && old_sha="$(cat "$DIR/current_sha")"

if [ "$MODE" = rollback ]; then
  [ "$BOOTSTRAP" = 0 ] || fail 80 "REFUSED: no PUBLIC_DC_API_KEY, nothing was ever deployed"
  [ -f "$DIR/previous_sha" ] || fail 80 "REFUSED: no previous release recorded"
  SHA="$(cat "$DIR/previous_sha")"
  TAG="${SHA:0:12}"
fi
REL="$DIR/releases/$TAG"

# Stage the release (rollback reuses the one on disk).
if [ "$MODE" != rollback ] && [ ! -f "$REL/.complete" ]; then
  rm -rf "$REL"; install -d -m 0755 "$REL"
  tar -xzf "/tmp/dc-prod-$TAG.tgz" -C "$REL"
  touch "$REL/.complete"
fi
rm -f "/tmp/dc-prod-$TAG.tgz"
[ -f "$REL/.complete" ] || fail 80 "REFUSED: release $TAG not on disk"

dcp() { # dcp <release> <tag> <sha> <compose args...>
  local rel="$1" t="$2" s="$3"
  shift 3
  ( cd "$rel" && IMAGE_TAG="$t" DEPLOYMENT_SHA="$s" docker compose -p "$PROJECT" --env-file "$ENV_FILE" \
    -f api/docker-compose.public.yml -f api/docker-compose.prod.yml "$@" )
}
# dcb: dcp with the bootstrap placeholders, scoped to this one command (first deployment).
# Compose interpolates the whole file; these values only satisfy that interpolation for
# api build, db start, migration and key creation. They are never written anywhere.
dcb() {
  if [ "$BOOTSTRAP" = 1 ]; then
    PUBLIC_DC_API_KEY=dc_anon_bootstrap CF_TUNNEL_TOKEN=bootstrap dcp "$@"
  else
    dcp "$@"
  fi
}
# Service containers only: `compose run` containers (imports, migration) carry the same
# service label, with oneoff=True.
cid() { docker ps -q --filter "label=com.docker.compose.project=$PROJECT" \
  --filter "label=com.docker.compose.service=$1" --filter "label=com.docker.compose.oneoff=False" | head -1; }
oneoff_api() { docker ps -q --filter "label=com.docker.compose.project=$PROJECT" \
  --filter "label=com.docker.compose.service=api" --filter "label=com.docker.compose.oneoff=True"; }
wait_healthy() { # wait_healthy <service>
  local s i c
  for i in $(seq 1 60); do
    c="$(cid "$1")"
    s=""; [ -z "$c" ] || s="$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || true)"
    [ "$s" = healthy ] && return 0
    sleep 3
  done
  echo "$1 not healthy (last state: ${s:-none})" >&2
  return 1
}
psql_db() { docker exec -i "$(cid db)" sh -c 'psql -X -q -At -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; }
# smoke <sha>: through the only published port (caddy loopback), the way cloudflared reaches it.
smoke() {
  local base=http://127.0.0.1:8080
  curl -fsS -m 10 -H "Host: $PROD_API_DOMAIN" "$base/v1/health/live" >/dev/null || { echo "smoke: $PROD_API_DOMAIN health" >&2; return 1; }
  curl -fsS -m 10 -H "Host: $PROD_DOMAIN" "$base/__version" | grep -q "\"deployment_sha\":\"$1\"" || { echo "smoke: $PROD_DOMAIN sha" >&2; return 1; }
  curl -fsS -m 10 -H "Host: $API_DOMAIN" "$base/v1/health/live" >/dev/null || { echo "smoke: $API_DOMAIN health" >&2; return 1; }
  curl -fsS -m 10 -H "Host: $APEX_DOMAIN" "$base/__version" | grep -q "\"deployment_sha\":\"$1\"" || { echo "smoke: $APEX_DOMAIN sha (frontend-prod)" >&2; return 1; }
  curl -sI -m 10 -H "Host: $PROD_DOMAIN" "$base/" | grep -qi '^strict-transport-security:' || { echo "smoke: $PROD_DOMAIN HSTS" >&2; return 1; }
  curl -sI -m 10 -H "Host: $APEX_DOMAIN" "$base/" | grep -qi '^strict-transport-security:' || { echo "smoke: $APEX_DOMAIN HSTS" >&2; return 1; }
  curl -sI -m 10 -H "Host: $API_DOMAIN" "$base/v1/health/live" | grep -qi '^x-robots-tag:.*noindex' || { echo "smoke: $API_DOMAIN x-robots-tag" >&2; return 1; }
}
base_rev() { echo "SELECT version_num FROM alembic_version" | psql_db | tr -d '\r'; }
# reads_base <release dir> <revision>: <revision> is the head of the migrations of the release.
reads_base() {
  local v="$1/api/backend/migrations/versions"
  [ -n "$2" ] || return 1
  grep -Eqs "^revision(: *str)? *= *[\"']$2[\"']" "$v"/*.py || return 1
  ! grep -Eqs "^down_revision.*[\"']$2[\"']" "$v"/*.py
}
# check_sources <release> <tag> <sha>: after the build, before anything starts.
check_sources() {
  local uid owner
  uid="$(dcb "$1" "$2" "$3" run --rm -T --no-deps --entrypoint id api -u | tr -d '\r')" ||
    fail 80 "REFUSED: cannot read the UID of the api image"
  owner="$(stat -c %u "$SOURCES")"
  [ "$uid" = "$owner" ] ||
    fail 80 "REFUSED: api image runs as UID $uid, $SOURCES belongs to UID $owner. do: chown $uid:$uid $SOURCES (as root), then run again"
  if [ ! -e "$SENTINEL" ]; then
    ( umask 027; printf '%s\n' "$SENTINEL_CONTENT" > "$SENTINEL" ) || fail 80 "REFUSED: cannot write $SENTINEL"
    echo "sentinel written: $SENTINEL" >&2
  fi
  [ "$(tr -d '[:space:]' < "$SENTINEL")" = "$(printf '%s' "$SENTINEL_CONTENT" | tr -d '[:space:]')" ] ||
    fail 80 "REFUSED: $SENTINEL does not hold '$SENTINEL_CONTENT'"
  dcb "$1" "$2" "$3" run --rm -T --no-deps --entrypoint sh api -c 'test -w /sources && test -r /sources/.datacommune-source-archive' ||
    fail 80 "REFUSED: /sources not writable by UID $uid in the api container"
  echo "sources: owner UID $owner = image UID $uid, sentinel ok, writable" >&2
}

if [ "$MODE" = deploy ]; then
  echo "build $TAG" >&2
  if [ "$BOOTSTRAP" = 1 ]; then
    dcb "$REL" "$TAG" "$SHA" build api || fail 81 "FAILED: build api; stack untouched"
  else
    dcp "$REL" "$TAG" "$SHA" build || fail 81 "FAILED: build; stack untouched"
  fi
  check_sources "$REL" "$TAG" "$SHA"
fi

if [ "$MODE" = deploy ]; then
  dcb "$REL" "$TAG" "$SHA" up -d --no-build --no-deps db || fail 81 "FAILED: db did not start; api untouched"
  wait_healthy db || fail 81 "FAILED: db not healthy; api untouched"

  # Tables that belong to no extension (spatial_ref_sys of PostGIS is not one of ours).
  user_tables="$(echo "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')
    AND n.nspname NOT LIKE 'pg\_%' AND NOT EXISTS (SELECT 1 FROM pg_depend d
    WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')" | psql_db | tr -d '\r')" ||
    fail 81 "FAILED: cannot read the base; api untouched"
  has_alembic="$(echo "SELECT to_regclass('public.alembic_version') IS NOT NULL" | psql_db | tr -d '\r')"
  if [ "$has_alembic" != t ]; then
    [ "$user_tables" = 0 ] ||
      fail 80 "REFUSED: $user_tables table(s) without alembic_version: base not created by this script nor restored"
    [ "$BOOTSTRAP" = 1 ] ||
      fail 80 "REFUSED: empty base but PUBLIC_DC_API_KEY is set: that key exists in no base here"
    echo "empty base: first deployment" >&2
  fi

  # An import (`compose run api ...`, plan 04.2-03) must not be stopped by a deploy.
  running="$(oneoff_api | tr '\n' ' ')"
  [ -z "$running" ] ||
    fail 80 "REFUSED: one-off api container(s) running ($running): an import is in progress; nothing stopped, deploy once it ends"

  # Room for the dump beside the base (04.1-DIMENSIONNEMENT.md counts no dump): at least the
  # size of the base free on the backups filesystem; a -Fc dump is smaller than the base.
  db_bytes="$(echo "SELECT pg_database_size(current_database())" | psql_db | tr -d '\r')" ||
    fail 81 "FAILED: size of the base unreadable; nothing migrated"
  free_bytes="$(df -P -B1 "$DIR/backups" | awk 'NR == 2 {print $4}')"
  case "$db_bytes$free_bytes" in ''|*[!0-9]*) fail 81 "FAILED: base size '$db_bytes' or free space '$free_bytes' unreadable; nothing migrated" ;; esac
  [ "$free_bytes" -ge "$db_bytes" ] ||
    fail 80 "REFUSED: $free_bytes bytes free in $DIR/backups, the base takes $db_bytes: no room for the pre-deploy dump; nothing stopped"

  # Written aside, read back whole by pg_restore, then named: a dump under a pre-*.pgdump name
  # is always complete. A .partial left by a killed run is stale (the lock rules out a live one).
  bk="$DIR/backups/pre-$TAG-$(date +%Y%m%dT%H%M%S).pgdump"
  find "$DIR/backups" -maxdepth 1 -name 'pre-*.pgdump.partial' -delete
  ( umask 077; docker exec "$(cid db)" sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$bk.partial" ) ||
    { rm -f "$bk.partial"; fail 81 "FAILED: pre-deploy dump; nothing migrated"; }
  docker exec -i "$(cid db)" pg_restore -f /dev/null < "$bk.partial" ||
    { rm -f "$bk.partial"; fail 81 "FAILED: pre-deploy dump unreadable by pg_restore; nothing migrated"; }
  mv "$bk.partial" "$bk"
  echo "pre-deploy dump: $bk (read back by pg_restore)" >&2
  # The old code must not serve while the schema changes (WR-02 of 04.1-REVIEW-CIBLEE.md).
  old_api="$(cid api)"
  if [ -n "$old_api" ]; then
    echo "stopping api $old_api (site down until the new release is healthy)" >&2
    docker stop "$old_api" >/dev/null 2>&1 || true
    if [ -n "$(cid api)" ]; then
      docker start "$old_api" >/dev/null 2>&1 || true
      fail 81 "FAILED: api still running after docker stop; nothing migrated"
    fi
  fi
  if ! dcb "$REL" "$TAG" "$SHA" run --rm -T --no-deps api alembic upgrade head >&2; then
    why="FAILED: alembic upgrade head (single transaction, rolled back): schema unchanged"
    [ -n "$old_api" ] || fail 81 "$why; no api was running"
    if docker start "$old_api" >/dev/null && wait_healthy api; then
      fail 81 "$why; previous api $old_api running again"
    fi
    fail 84 "UPGRADE_FAILED_API_DOWN: $why; previous api $old_api does not come back. do: infra/prod/deploy.sh ${old_sha:-<served commit>}"
  fi

  if [ "$BOOTSTRAP" = 1 ]; then
    # Real anonymous key, created by the existing CLI in this base; never displayed.
    grep -q '^PUBLIC_DC_API_KEY=' "$ENV_FILE" && fail 80 "REFUSED: PUBLIC_DC_API_KEY appeared in $ENV_FILE meanwhile"
    keyout="$(umask 077; mktemp "$DIR/.anon-key.XXXXXX")"   # shredded by cleanup on every exit
    printf 'ROTATE\n' | dcb "$REL" "$TAG" "$SHA" run --rm -T --no-deps -e ANON_KEY_QUOTA="$ANON_KEY_QUOTA" \
      api python -m scripts.rotate_anon_key >"$keyout" 2>&1 ||
      fail 81 "FAILED: rotate_anon_key (output kept private, shredded); nothing served"
    nkeys="$(grep -cE '^dc_anon_[A-Za-z0-9_-]{20,}$' "$keyout" || true)"
    [ "$nkeys" = 1 ] ||
      fail 81 "FAILED: rotate_anon_key printed $nkeys dc_anon_ line(s), expected 1; output shredded. Revoke any anonymous-bundle-key just created (admin), then run again"
    # printf is a builtin: the key is in no process argument list. Ends the last line first.
    ( umask 077
      [ -z "$(tail -c1 "$ENV_FILE")" ] || printf '\n' >> "$ENV_FILE"
      printf 'PUBLIC_DC_API_KEY=%s\n' "$(grep -E '^dc_anon_[A-Za-z0-9_-]{20,}$' "$keyout")" >> "$ENV_FILE" )
    cleanup; keyout=""
    BOOTSTRAP=0
    env_check "$ENV_FILE" 0 || fail 81 "FAILED: $ENV_FILE after the key was added (above)"
    echo "anonymous key created by the CLI, written to $ENV_FILE, never displayed" >&2
    # From here compose reads the real key and the real token from the private file.
    dcp "$REL" "$TAG" "$SHA" build frontend frontend-prod || fail 81 "FAILED: build of the frontends; nothing served"
  fi
fi

if [ "$MODE" = rollback ]; then
  rev="$(base_rev)" || fail 81 "FAILED: revision of the base unreadable; nothing changed"
  reads_base "$REL" "$rev" ||
    fail 80 "REFUSED: base at revision ${rev:-?}, not the head of release $TAG: its code would serve a schema it does not know. Restore the pre-deploy dump pre-<sha12>-*.pgdump of the release that migrated (README, Retour arrière), then run again"
fi

# --no-tunnel: cloudflared scaled to 0 (stopped if it was running), nothing reaches the public.
up_args=(up -d --no-build); [ "$NO_TUNNEL" = 1 ] && up_args+=(--scale cloudflared=0)
switch_ok=1
dcp "$REL" "$TAG" "$SHA" "${up_args[@]}" >&2 && wait_healthy api && wait_healthy frontend &&
  wait_healthy frontend-prod && smoke "$SHA" || switch_ok=0

if [ "$switch_ok" = 0 ]; then
  echo "release $TAG unhealthy" >&2
  [ "$MODE" = rollback ] && fail 91 "ROLLBACK_FAILED: previous release $TAG does not come up"
  [ -n "$old_sha" ] && [ "$old_sha" != "$SHA" ] || fail 86 "UNHEALTHY: $TAG, no previous release to return to"
  OT="${old_sha:0:12}"; OR="$DIR/releases/$OT"
  [ -f "$OR/.complete" ] || fail 91 "ROLLBACK_FAILED: previous release $OT not on disk"
  rev="$(base_rev)" || rev=""
  reads_base "$OR" "$rev" ||
    fail 87 "MIGRATED_UNHEALTHY: base at revision ${rev:-unreadable}, beyond release $OT; $TAG unhealthy, $OT NOT restarted (its code does not read this schema). do: restore $bk (README, Retour arrière), then infra/prod/deploy.sh $old_sha"
  echo "returning to $OT" >&2
  if dcp "$OR" "$OT" "$old_sha" "${up_args[@]}" >&2 && wait_healthy api && wait_healthy frontend &&
    wait_healthy frontend-prod && smoke "$old_sha"; then
    fail 90 "ROLLED_BACK to $OT (schema at $rev, read by $OT)"
  fi
  fail 91 "ROLLBACK_FAILED: $OT does not come up either"
fi

if [ "$old_sha" != "$SHA" ]; then
  [ -z "$old_sha" ] || echo "$old_sha" > "$DIR/previous_sha"
  echo "$SHA" > "$DIR/current_sha"
fi
prev12=""; [ ! -f "$DIR/previous_sha" ] || prev12="$(cut -c1-12 "$DIR/previous_sha")"
docker images --format '{{.Repository}}:{{.Tag}}' | grep '^datacommune-prod-' |
  grep -v -e ":$TAG\$" -e ":${prev12:-none}\$" | xargs -r docker rmi >/dev/null 2>&1 || true
for d in "$DIR"/releases/*; do
  b="$(basename "$d")"
  [ "$b" = "$TAG" ] || [ "$b" = "$prev12" ] || rm -rf "$d"
done
docker image prune -f >/dev/null 2>&1 || true
# Pre-deploy dumps are pruned only once a release is healthy: a failed or unhealthy deploy
# keeps every dump, among them the last one taken before its migration. Newest 3 kept.
{ ls -1t "$DIR"/backups/pre-*.pgdump 2>/dev/null || true; } | tail -n +4 | xargs -r rm -f
echo "DEPLOYED $SHA"
trap - EXIT
state_exit 0
IN_SERVER

rc=0
printf '%s\n%s\n' "$ENV_CHECK_FN" "$SERVER_BODY" |
  remote_script "MODE=$mode" "SHA=$sha" "TAG=$tag" "DIR=$PROD_DIR" "ENV_FILE=$PROD_ENV_FILE" \
    "PROJECT=$PROD_PROJECT" "NO_TUNNEL=$no_tunnel" "ANON_KEY_QUOTA=$ANON_KEY_QUOTA" \
    "PROD_DOMAIN=$PROD_DOMAIN" "PROD_API_DOMAIN=$PROD_API_DOMAIN" \
    "APEX_DOMAIN=$APEX_DOMAIN" "API_DOMAIN=$API_DOMAIN" || rc=$?
exit "$rc"
