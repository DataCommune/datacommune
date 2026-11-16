#!/usr/bin/env bash
# Build and deploy a commit on the public instance CT, or roll back to the previous release.
#
#   infra/public/public-deploy.sh [--dry-run] [<commit>]       # default: HEAD
#   infra/public/public-deploy.sh [--dry-run] --db-only [<commit>]
#   infra/public/public-deploy.sh [--dry-run] --rollback
#   --no-tunnel (with a deploy or --rollback): cloudflared stays stopped, so nothing is public;
#     the stack is reachable only through the caddy loopback of the CT (first deploy, checks).
#   --allow-unpublished (deploy or --db-only): deploy a commit that is not on the public master
#     (emergency only; printed as a WARNING). Without it, a commit is deployed only when it is an
#     ancestor of $PUBLIC_PUBLISHED_REF (default refs/remotes/public/master, D-135: nothing is
#     served that is not on the public master): refused (80) otherwise, or when that ref is
#     unknown (`git fetch public` first). --rollback goes back to a release already served.
#   --backup-ref file:<host path> (deploy only): the pre-deploy dump of step 4 is replaced by
#     a dump of this same base the caller already holds and checked; printed and written next
#     to the dumps as pre-<sha12>-<date>.ref. Only the rollback of public-db-restore.sh
#     --from-zfs-clone passes it (its pre-national dump of the base of before).
# A base on a ZFS clone (CT pgdata mounted from $PUBLIC_ZFS_PARENT/pg-*, public-db-restore.sh
# --from-zfs-clone) is not dumped: its 144 GB would not fit the rootfs of the CT (04.2-05).
# The CT block first runs up to its last refusal (PRECHECK=1: preconditions, build, db,
# restored base), then a snapshot <clone>@pre-<sha12>-<UTC date> is taken on the host, the api
# still serving, and replaces the dump (reference printed and written as for --backup-ref);
# a refused deploy leaves none. These snapshots go with the clone (public-db-restore.sh
# --drop-previous). --dry-run reads nothing remote and prints this step.
#
# Run on the workstation. The code reaches the CT as `git archive <sha> api frontend`
# (no GitHub credential enters the CT), extracted in $PUBLIC_DIR/releases/<sha12>.
#
# Deploy, in the CT, in this order:
#   1. preconditions on $PUBLIC_ENV_FILE (mode 600; CF_TUNNEL_TOKEN set; PUBLIC_DC_API_KEY
#      starts with dc_anon_) -- names only, values are never printed;
#   2. `compose build` (images datacommune-public-{api,frontend}:<sha12>), stack still running;
#   3. `up -d db`, wait healthy; the database must already be restored
#      (public-db-restore.sh), else refused;
#   4. pg_dump -Fc of the database to $PUBLIC_DIR/backups (last 3 kept), unless --backup-ref.
#      Space guard first, the api still serving: refused (80) when the free space of the
#      backups directory is below pg_database_size x PUBLIC_DUMP_SIZE_FACTOR (default 0.5)
#      + PUBLIC_DUMP_RESERVE_MB (default 2048); the dump is written as .part, its PGDMP header
#      checked, then renamed; a failed one is deleted. Then the api of the old
#      release is stopped (checked: no api container left running), then `alembic upgrade
#      head` in a one-off api container: the old code never reads a schema it does not know
#      (a migration need not be backward compatible, e.g. 106). The site is down (api) from
#      the stop to step 5. A failed upgrade rolls back its single transaction and the old api
#      container is started again;
#   5. `up -d --no-build`, wait api and frontend healthy;
#   6. smoke through the caddy loopback (health, deployed sha, security headers, noindex);
#   7. state: current_sha / previous_sha; old images and releases pruned (current + previous kept).
# --db-only stages the release and starts the db only (needed before public-db-restore.sh;
# compose is given placeholder values for the variables of the services that stay down).
# The schema is never downgraded. A release is started again (automatic return after an
# unhealthy switch, --rollback) only if the revision of the base is the head of its own
# migrations; otherwise nothing is started (87, or 80 for --rollback) and the way back is the
# pre-deploy dump `pre-<sha12>-*.pgdump` (README, "Rollback").
#
# Exit codes: 0 deployed | 2 usage | 80 refused, nothing changed | 81 failed before the
# switch (build, db, dump, api stop, migration): schema unchanged, the old api serves again |
# 84 migration failed (schema unchanged) and the old api does not come back | 86 new release
# unhealthy and no previous release | 87 new release unhealthy, schema migrated beyond the
# previous release: nothing restarted, restore the pre-deploy dump | 90 new release
# unhealthy, previous restarted and healthy (schema unchanged by the deploy) |
# 91 rollback failed (diagnose: `docker compose -p datacommune-public ps`).
# The CT block prints PUBLIC_EXIT=<code>; an ssh failure without that line is a transport failure.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$here/lib.sh"

mode=deploy rev=HEAD no_tunnel=0 backup_ref="" allow_unpublished=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --backup-ref) backup_ref="${2:-}"; shift 2 || { log "--backup-ref file:<path>"; exit 2; } ;;
    --db-only) mode=dbonly; shift ;;
    --rollback) mode=rollback; shift ;;
    --no-tunnel) no_tunnel=1; shift ;;
    --allow-unpublished) allow_unpublished=1; shift ;;
    --ctid) PUBLIC_CTID="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,62p' "$0"; exit 0 ;;
    -*) log "unknown argument: $1"; exit 2 ;;
    *) rev="$1"; shift ;;
  esac
done
check_ids
if [ -n "$backup_ref" ]; then
  [ "$mode" = deploy ] || { log "--backup-ref is a deploy option"; exit 2; }
  [[ "$backup_ref" =~ ^file:/[A-Za-z0-9/._:-]+$ ]] || { log "--backup-ref must look like file:/<path>"; exit 2; }
fi
dump_factor="${PUBLIC_DUMP_SIZE_FACTOR:-0.5}" dump_reserve_mb="${PUBLIC_DUMP_RESERVE_MB:-2048}"
dump_factor_ok "$dump_factor" || { log "PUBLIC_DUMP_SIZE_FACTOR must be a number above 0 (0.5), got '$dump_factor'"; exit 2; }
need_numeric PUBLIC_DUMP_RESERVE_MB "$dump_reserve_mb"

sha="" tag=""
if [ "$mode" != rollback ]; then
  repo_root="$(git -C "$here" rev-parse --show-toplevel)"
  sha="$(git -C "$repo_root" rev-parse --verify --quiet "$rev^{commit}")" ||
    { log "unknown commit: $rev"; exit 2; }
  tag="${sha:0:12}"
  published_ref="${PUBLIC_PUBLISHED_REF:-refs/remotes/public/master}"
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
  archive="$(mktemp -d)/release-$tag.tgz"
  remote_tgz="/tmp/dc-public-$tag.tgz"
  log "release: $sha ($mode)"
  local_run git -C "$repo_root" archive --format=tar.gz -o "$archive" "$sha" api frontend
  local_run scp -q -o BatchMode=yes "$archive" "$PUBLIC_SSH_HOST:$remote_tgz"
  pve "pct push $PUBLIC_CTID $remote_tgz $remote_tgz && rm -f $remote_tgz"
  rmdir "$(dirname "$archive")" 2>/dev/null || true  # dry-run leaves it empty; a real run keeps the file
  [ "$DRY_RUN" = 1 ] || { rm -f "$archive"; rmdir "$(dirname "$archive")" 2>/dev/null || true; }
fi

# The block run in the CT (run_ct <precheck 0|1>): PRECHECK=1 stops after its refusals.
IFS= read -r -d '' CT_BLOCK <<'IN_CT' || true
state_exit() { echo "PUBLIC_EXIT=$1"; }
fail() { echo "$2" >&2; trap - EXIT; state_exit "$1"; exit "$1"; }
trap 'rc=$?; [ "$rc" -eq 0 ] || state_exit "$rc"' EXIT

install -d -m 0755 "$DIR" "$DIR/releases"
install -d -m 0700 "$DIR/backups"
[ -f "$ENV_FILE" ] || fail 80 "REFUSED: $ENV_FILE missing"
[ "$(stat -c %a "$ENV_FILE")" = 600 ] || fail 80 "REFUSED: $ENV_FILE must be mode 600"
envval() { grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true; }  # never printed

old_sha=""
[ -f "$DIR/current_sha" ] && old_sha="$(cat "$DIR/current_sha")"

if [ "$MODE" = rollback ]; then
  [ -f "$DIR/previous_sha" ] || fail 80 "REFUSED: no previous release recorded"
  SHA="$(cat "$DIR/previous_sha")"
  TAG="${SHA:0:12}"
fi
REL="$DIR/releases/$TAG"

# Stage the release (MODE=rollback reuses the one on disk).
if [ "$MODE" != rollback ] && [ ! -f "$REL/.complete" ]; then
  rm -rf "$REL"; install -d -m 0755 "$REL"
  tar -xzf "/tmp/dc-public-$TAG.tgz" -C "$REL"
  touch "$REL/.complete"
fi
rm -f "/tmp/dc-public-$TAG.tgz"
[ -f "$REL/.complete" ] || fail 80 "REFUSED: release $TAG not on disk"

if [ "$MODE" = dbonly ]; then
  # Interpolation covers the whole file: placeholders for the services that stay down.
  export PUBLIC_DC_API_KEY=dc_anon_bootstrap CF_TUNNEL_TOKEN=bootstrap
else
  [ -n "$(envval CF_TUNNEL_TOKEN)" ] || fail 80 "REFUSED: CF_TUNNEL_TOKEN not set in $ENV_FILE (Cloudflare step of the README)"
  case "$(envval PUBLIC_DC_API_KEY)" in
    dc_anon_?*) ;;
    *) fail 80 "REFUSED: PUBLIC_DC_API_KEY in $ENV_FILE must start with dc_anon_ (rotate_anon_key.py, README)" ;;
  esac
fi

dcp() { # dcp <release> <tag> <sha> <compose args...>
  local rel="$1" t="$2" s="$3"
  shift 3
  # From inside the release: compose 5 (bake) refuses a build context outside the
  # working directory ("additional privileges requested: --allow=fs.read=...").
  ( cd "$rel" && IMAGE_TAG="$t" DEPLOYMENT_SHA="$s" docker compose -p "$PROJECT" --env-file "$ENV_FILE" \
    -f "$rel/api/docker-compose.public.yml" "$@" )
}
cid() { docker ps -q --filter "label=com.docker.compose.project=$PROJECT" \
  --filter "label=com.docker.compose.service=$1" | head -1; }
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
smoke() { # smoke <sha>: through the caddy loopback, the way cloudflared reaches it
  local base=http://127.0.0.1:8080
  curl -fsS -m 10 -H 'Host: api.datacommune.fr' "$base/v1/health/live" >/dev/null || return 1
  curl -fsS -m 10 -H 'Host: datacommune.fr' "$base/__version" | grep -q "\"deployment_sha\":\"$1\"" || return 1
  curl -sI -m 10 -H 'Host: datacommune.fr' "$base/" | grep -qi '^strict-transport-security:' || return 1
  curl -sI -m 10 -H 'Host: api.datacommune.fr' "$base/v1/health/live" | grep -qi '^x-robots-tag:.*noindex' || return 1
}
base_rev() { echo "SELECT version_num FROM alembic_version" | psql_db | tr -d '\r'; }
# reads_base <release dir> <revision>: <revision> is the head of the migrations of the
# release (defined there, the down_revision of none), so its code reads the schema as it is.
reads_base() {
  local v="$1/api/backend/migrations/versions"
  [ -n "$2" ] || return 1
  grep -Eqs "^revision(: *str)? *= *[\"']$2[\"']" "$v"/*.py || return 1
  ! grep -Eqs "^down_revision.*[\"']$2[\"']" "$v"/*.py
}

if [ "$MODE" != rollback ] && [ "$MODE" != dbonly ]; then
  echo "build $TAG" >&2
  dcp "$REL" "$TAG" "$SHA" build || fail 81 "FAILED: build; stack untouched"
fi

if [ "$MODE" != rollback ]; then
  dcp "$REL" "$TAG" "$SHA" up -d --no-build --no-deps db || fail 81 "FAILED: db did not start; api untouched"
  wait_healthy db || fail 81 "FAILED: db not healthy; api untouched"
fi
[ "$MODE" != dbonly ] || { echo "DBONLY db up, release $TAG staged"; state_exit 0; trap - EXIT; exit 0; }

if [ "$MODE" = deploy ]; then
  [ "$(echo "SELECT to_regclass('public.alembic_version') IS NOT NULL" | psql_db)" = t ] ||
    fail 80 "REFUSED: database not restored (no alembic_version); run public-db-restore.sh"
  # PRECHECK=1 (a base on a clone): every refusal has passed, the snapshot can be taken.
  [ "$PRECHECK" != 1 ] || { echo "PRECHECK_OK $TAG" >&2; trap - EXIT; exit 0; }
  stamp_bk="pre-$TAG-$(date +%Y%m%dT%H%M%S)"
  if [ -n "$BACKUP_REF" ]; then
    # A snapshot of the clone, or a dump held by the caller (04.2-05: a 144 GB dump fills the rootfs).
    bk="$BACKUP_REF"
    echo "$BACKUP_REF" > "$DIR/backups/$stamp_bk.ref"
    ls -1t "$DIR"/backups/pre-*.ref | tail -n +4 | xargs -r rm -f
    echo "pre-deploy dump replaced by $BACKUP_REF" >&2
  else
    # Space guard while the api still serves: refuse rather than fail with the site down.
    rm -f "$DIR"/backups/pre-*.pgdump.part  # left by a dump killed with its shell
    size="$(echo "SELECT pg_database_size(current_database())" | psql_db | tr -d '\r ')" || size=""
    case "$size" in ''|*[!0-9]*) fail 80 "REFUSED: size of the database unreadable; nothing changed" ;; esac
    need="$(awk -v s="$size" -v f="$DUMP_FACTOR" -v r="$DUMP_RESERVE_MB" 'BEGIN { printf "%.0f", s * f + r * 1048576 }')"
    free="$(df -B1 --output=avail "$DIR/backups" | tail -n 1 | tr -d ' ')" || free=""
    case "$free" in ''|*[!0-9]*) fail 80 "REFUSED: free space of $DIR/backups unreadable; nothing changed" ;; esac
    [ "$free" -ge "$need" ] ||
      fail 80 "REFUSED: pre-deploy dump needs ~$need bytes (database $size x $DUMP_FACTOR + ${DUMP_RESERVE_MB} MB), $free free in $DIR/backups; nothing changed"
    bk="$DIR/backups/$stamp_bk.pgdump"
    if ! ( umask 077; docker exec "$(cid db)" sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$bk.part" ) ||
      [ "$(head -c 5 "$bk.part")" != PGDMP ]; then
      rm -f "$bk.part"
      fail 81 "FAILED: pre-deploy dump (partial file deleted); nothing migrated"
    fi
    mv "$bk.part" "$bk"
    ls -1t "$DIR"/backups/pre-*.pgdump | tail -n +4 | xargs -r rm -f
    echo "pre-deploy dump: $bk" >&2
  fi
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
  if ! dcp "$REL" "$TAG" "$SHA" run --rm --no-deps api alembic upgrade head >&2; then
    why="FAILED: alembic upgrade head (single transaction, rolled back): schema unchanged"
    [ -n "$old_api" ] || fail 81 "$why; no api was running"
    if docker start "$old_api" >/dev/null && wait_healthy api; then
      fail 81 "$why; previous api $old_api running again"
    fi
    fail 84 "UPGRADE_FAILED_API_DOWN: $why; previous api $old_api does not come back. do: infra/public/public-deploy.sh ${old_sha:-<served commit>}"
  fi
fi

if [ "$MODE" = rollback ]; then
  rev="$(base_rev)" || fail 81 "FAILED: revision of the base unreadable; nothing changed"
  reads_base "$REL" "$rev" ||
    fail 80 "REFUSED: base at revision ${rev:-?}, not the head of release $TAG: its code would serve a schema it does not know. Restore the pre-deploy dump pre-<sha12>-*.pgdump of the release that migrated (README, Rollback), then run again"
fi

# --no-tunnel: cloudflared scaled to 0 (stopped if it was running), nothing reaches the public.
up_args=(up -d --no-build); [ "$NO_TUNNEL" = 1 ] && up_args+=(--scale cloudflared=0)
switch_ok=1
dcp "$REL" "$TAG" "$SHA" "${up_args[@]}" >&2 && wait_healthy api && wait_healthy frontend && smoke "$SHA" || switch_ok=0

if [ "$switch_ok" = 0 ]; then
  echo "release $TAG unhealthy" >&2
  [ "$MODE" = rollback ] && fail 91 "ROLLBACK_FAILED: previous release $TAG does not come up"
  [ -n "$old_sha" ] && [ "$old_sha" != "$SHA" ] || fail 86 "UNHEALTHY: $TAG, no previous release to return to"
  OT="${old_sha:0:12}"; OR="$DIR/releases/$OT"
  [ -f "$OR/.complete" ] || fail 91 "ROLLBACK_FAILED: previous release $OT not on disk"
  rev="$(base_rev)" || rev=""
  case "$bk" in
    zfs:*) how="roll the clone back to the snapshot ${bk#zfs:}" ;;
    file:*) how="restore the dump ${bk#file:} (on the host)" ;;
    *) how="restore $bk" ;;
  esac
  reads_base "$OR" "$rev" ||
    fail 87 "MIGRATED_UNHEALTHY: base at revision ${rev:-unreadable}, beyond release $OT; $TAG unhealthy, $OT NOT restarted (its code does not read this schema). do: $how (README, Rollback), then infra/public/public-deploy.sh $old_sha"
  echo "returning to $OT" >&2
  if dcp "$OR" "$OT" "$old_sha" "${up_args[@]}" >&2 && wait_healthy api && wait_healthy frontend && smoke "$old_sha"; then
    fail 90 "ROLLED_BACK to $OT (schema at $rev, read by $OT)"
  fi
  fail 91 "ROLLBACK_FAILED: $OT does not come up either"
fi

if [ "$old_sha" != "$SHA" ]; then
  [ -z "$old_sha" ] || echo "$old_sha" > "$DIR/previous_sha"
  echo "$SHA" > "$DIR/current_sha"
fi
prev12=""; [ ! -f "$DIR/previous_sha" ] || prev12="$(cut -c1-12 "$DIR/previous_sha")"
docker images --format '{{.Repository}}:{{.Tag}}' | grep '^datacommune-public-' |
  grep -v -e ":$TAG\$" -e ":${prev12:-none}\$" | xargs -r docker rmi >/dev/null 2>&1 || true
for d in "$DIR"/releases/*; do
  b="$(basename "$d")"
  [ "$b" = "$TAG" ] || [ "$b" = "$prev12" ] || rm -rf "$d"
done
docker image prune -f >/dev/null 2>&1 || true
echo "DEPLOYED $SHA"
trap - EXIT
state_exit 0
IN_CT
run_ct() {
  ct_script "$PUBLIC_CTID" \
    "MODE=$mode" "SHA=$sha" "TAG=$tag" "DIR=$PUBLIC_DIR" "ENV_FILE=$PUBLIC_ENV_FILE" \
    "PROJECT=$PUBLIC_PROJECT" "NO_TUNNEL=$no_tunnel" "BACKUP_REF=$backup_ref" "PRECHECK=$1" \
    "DUMP_FACTOR=$dump_factor" "DUMP_RESERVE_MB=$dump_reserve_mb" <<<"$CT_BLOCK"
}

# A base on a ZFS clone: snapshot of the clone instead of the pre-deploy dump (header).
if [ "$mode" = deploy ] && [ -z "$backup_ref" ] && [ "$DRY_RUN" = 1 ]; then
  printf '+ ssh %s: if the pgdata of CT %s is mounted from %s/pg-*: CT block until its refusals, then zfs snapshot <clone>@pre-%s-<date> instead of the pre-deploy dump\n' \
    "$PUBLIC_SSH_HOST" "$PUBLIC_CTID" "$PUBLIC_ZFS_PARENT" "$tag"
elif [ "$mode" = deploy ] && [ -z "$backup_ref" ]; then
  clone="$(host_script read "CTID=$PUBLIC_CTID" "ZPARENT=$PUBLIC_ZFS_PARENT" \
    "PGDATA=/var/lib/docker/volumes/${PUBLIC_PROJECT}_pgdata/_data" <<'HOST'
src="$(pct config "$CTID" | tr -d '\r' | awk -v t="mp=$PGDATA" '/^mp[0-9]+: / {
  n = split($0, f, ","); s = f[1]; sub(/^mp[0-9]+: /, "", s); for (i = 2; i <= n; i++) if (f[i] == t) print s }')"
case "$src" in "/$ZPARENT"/pg-*) ;; *) exit 0 ;; esac
# A clone of the switch: an error here must not fall back to the dump.
name="$(zfs list -H -o name "$src")" || { echo "zfs list $src failed" >&2; exit 1; }
[ "/$name" = "$src" ] || { echo "$src is not the mountpoint of $name" >&2; exit 1; }
echo "$name"
HOST
)" || { log "REFUSED: cannot read the pgdata mount of CT $PUBLIC_CTID; nothing changed"; exit 80; }
  if [ -n "$clone" ]; then
    # The refusals of the CT block first: a refused deploy leaves no snapshot behind.
    run_ct 1 || exit $?
    snap="$clone@pre-$tag-$(date -u +%Y%m%dT%H%M%SZ)"
    host_script write "SNAP=$snap" <<'HOST' || { log "FAILED: snapshot $snap; nothing changed"; exit 81; }
zfs snapshot "$SNAP"
HOST
    backup_ref="zfs:$snap"
    log "base on the clone $clone: pre-deploy backup = snapshot $snap"
  fi
fi

rc=0
run_ct 0 || rc=$?
exit "$rc"
