#!/usr/bin/env bash
# National import, once, dataset after dataset (plan 04.2-03).
#
#   import.sh [--dry-run]                      the 21 datasets, in order
#   import.sh [--dry-run] --resume <dataset>   from <dataset>, the earlier ones must be success
#   import.sh [--dry-run] --replay <dataset> <run_id>   one archived run (archive_replay only)
#   import.sh [--dry-run] --setup-repo <bundle>         once: $PROD_DIR/repo.git from a git bundle
#
# Runs ON the server, as datacommune, against the deployed release ($PROD_DIR/current_sha,
# $PROD_DIR/releases/<sha12>): the sequence lasts about a day and must not depend on an ssh
# session (README § 10: nohup). --dry-run also works on the workstation: it calls nothing.
#
# Order: SMALL then BIG. BAN comes before DPE (orphans attached by raw address) and DPE early,
# so that its transient (~360 GB) happens on a nearly empty disk. The names are the
# dataset_name of the pipelines (run_etl), not the scope catalogue keys. No --from-archive by
# default: the first national import downloads from the producers and archives on /sources.
#
# Per dataset (same contract as the hypervisor's workbase-import.sh, without pct):
#   1. etl_runs max(id) before the launch (psql in the db container, credentials stay there);
#   2. every IMPORT_SAMPLE_INTERVAL s (default 30) in the background: `df -B1 --output=used /`,
#      `du -sb` of pg_wal in the db container, memory.peak of the run container (cgroup v2,
#      kernel maximum, read inside the container) — sampled maxima, not real peaks;
#   3. `docker compose run --rm -T --name dc-import-<dataset> api python -m run_etl <dataset>`,
#      log in $PROD_DIR/imports/<dataset>/logs/<run_id>.log;
#   4. receipt $PROD_DIR/imports/<dataset>/<run_id>.json and pointer latest.json by
#      scripts/export_import_manifest.py, in the API image plus git (tag <sha12>-tool, built
#      once, like the hypervisor's wb-tool.sh) with the bare repository $PROD_DIR/repo.git mounted
#      on /repo: the exporter checks repo_sha against the git history, which a release made
#      by `git archive` does not carry;
#   5. measurements <run_id>.mesures.json beside it (never rewritten);
#   6. `IMPORT <dataset> success|failed run_id=<id> ...`; anything but success stops the
#      sequence (exit 1). After the last one: IMPORT_SEQUENCE_OK.
# Refusals: unknown dataset or malformed run id (2) before anything; --replay of a pipeline
# without archive_replay (80); before any run_etl (80): python3 absent, release absent, an
# import container already running, receipts already present without --resume, the deployed
# commit absent from $PROD_DIR/repo.git. --resume <dataset>: every earlier dataset must have
# latest.json naming a receipt whose etl_run.status is success, else exit 1 before any run.
set -euo pipefail

# shellcheck source=import-order.sh
. "$(dirname "${BASH_SOURCE[0]}")/import-order.sh"
readonly WAL_DIR=/var/lib/postgresql/data/pg_wal
readonly NOT_MEASURED="non mesuré"

PROD_DIR="${PROD_DIR:-/opt/datacommune}"
PROJECT="${PROD_PROJECT:-datacommune-prod}"
ENV_FILE="${PROD_ENV_FILE:-$PROD_DIR/.env}"
INTERVAL="${IMPORT_SAMPLE_INTERVAL:-30}"
IMPORTS="$PROD_DIR/imports"

usage() {
  echo "usage: $0 [--dry-run] [--resume <dataset> | --replay <dataset> <run_id> | --setup-repo <bundle>]" >&2
  exit 2
}
refuse() { echo "REFUSED: $*" >&2; exit 80; }
in_list() { local x="$1" y; shift; for y in "$@"; do [ "$x" = "$y" ] && return 0; done; return 1; }
known() { in_list "$1" "${ORDER[@]}" || { echo "jeu inconnu: $1 (attendu : ${ORDER[*]})" >&2; exit 2; }; }

dry_run=0 resume="" replay="" replay_id="" setup_bundle=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1; shift ;;
    --setup-repo) [ $# -ge 2 ] || usage; setup_bundle="$2"; shift 2 ;;
    --resume) [ $# -ge 2 ] || usage; resume="$2"; shift 2 ;;
    --replay) [ $# -ge 3 ] || usage; replay="$2"; replay_id="$3"; shift 3 ;;
    *) usage ;;
  esac
done
[ -z "$resume" ] || [ -z "$replay" ] || usage
[ -z "$setup_bundle" ] || { [ -z "$resume" ] && [ -z "$replay" ]; } || usage
[[ "$INTERVAL" =~ ^[1-9][0-9]*$ ]] || { echo "IMPORT_SAMPLE_INTERVAL: integer >= 1" >&2; exit 2; }
[ -z "$resume" ] || known "$resume"
if [ -n "$replay" ]; then
  known "$replay"
  [[ "$replay_id" =~ ^[1-9][0-9]*-[0-9]{8}T[0-9]{6}Z$ ]] ||
    { echo "run_id inconnu: format attendu <etl_runs.id>-<AAAAMMJJTHHMMSSZ>" >&2; exit 2; }
  in_list "$replay" "${REPLAYABLE[@]}" ||
    { echo "archive_replay_non_supporte: $replay (seuls ${REPLAYABLE[*]})" >&2; exit 80; }
fi

# success_receipt <dataset>: latest.json names a receipt of that dataset whose run succeeded.
success_receipt() {
  python3 - "$IMPORTS/$1" "$1" <<'PY' 2>/dev/null
import json, sys
from pathlib import Path
d, ds = Path(sys.argv[1]), sys.argv[2]
ptr = json.loads((d / "latest.json").read_text(encoding="utf-8"))
rec = json.loads((d / ptr["manifest"]).read_text(encoding="utf-8"))
ok = ptr["dataset"] == rec["dataset"] == ds and rec["etl_run"]["status"] == "success"
sys.exit(0 if ok else 1)
PY
}

# Datasets of this invocation, and the extra run_etl arguments.
selected=() extra=()
if [ -n "$replay" ]; then
  selected=("$replay"); extra=(--from-archive "$replay_id")
elif [ -n "$resume" ]; then
  started=0
  for ds in "${ORDER[@]}"; do
    [ "$ds" = "$resume" ] && started=1
    if [ "$started" = 1 ]; then selected+=("$ds"); continue; fi
    success_receipt "$ds" || { echo "reçu success absent: $ds (avant $resume) ; séquence arrêtée" >&2; exit 1; }
    echo "SKIP $ds (reçu success)"
  done
else
  selected=("${ORDER[@]}")
fi

if [ -f "$PROD_DIR/current_sha" ]; then sha="$(tr -d '[:space:]' <"$PROD_DIR/current_sha")"
elif [ "$dry_run" = 1 ]; then sha="<current_sha>"
else refuse "$PROD_DIR/current_sha absent: deploy first (infra/prod/deploy.sh)"; fi
tag="${sha:0:12}"
release="$PROD_DIR/releases/$tag"

if [ "$dry_run" = 1 ] && [ -n "$setup_bundle" ]; then
  echo "+ build datacommune-prod-api:$tag-tool if absent (API image + git)"
  echo "+ git clone --bare $setup_bundle $PROD_DIR/repo.git (in that image), then git cat-file -e $sha^{commit}"
  echo "DRY_RUN import setup-repo"
  exit 0
fi
if [ "$dry_run" = 1 ]; then
  for ds in "${selected[@]}"; do
    echo "+ docker compose -p $PROJECT run --rm -T --name dc-import-$ds api python -m run_etl $ds ${extra[*]}"
    echo "+   receipt $IMPORTS/$ds/<run_id>.json, measurements <run_id>.mesures.json (df /, pg_wal, memory.peak every ${INTERVAL}s)"
  done
  echo "DRY_RUN import datasets=${#selected[@]} release=$release"
  exit 0
fi

# --- preconditions -----------------------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || refuse "python3 absent (receipts are read with it)"
[ -d "$release/api" ] || refuse "release absente: $release"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || refuse "current_sha malformé: $sha"

dc() {
  (cd "$release" && IMAGE_TAG="${DC_TAG:-$tag}" DEPLOYMENT_SHA="$sha" docker compose -p "$PROJECT" \
    --env-file "$ENV_FILE" -f api/docker-compose.public.yml -f api/docker-compose.prod.yml "$@")
}
# q <sql>: one value per line, from the db container (its own credentials, never on this side).
q() {
  # shellcheck disable=SC2016
  printf '%s\n' "$1" | dc exec -T db sh -c 'psql -X -q -tA -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}

if [ -n "$setup_bundle" ]; then
  [ -f "$setup_bundle" ] || refuse "paquet git absent: $setup_bundle"
  [ ! -e "$PROD_DIR/repo.git" ] || refuse "$PROD_DIR/repo.git existe déjà (rien n'est remplacé)"
fi
tool_tag="$tag-tool"
if ! docker image inspect "datacommune-prod-api:$tool_tag" >/dev/null 2>&1 </dev/null; then
  docker build -q -t "datacommune-prod-api:$tool_tag" - >&2 <<DOCKERFILE
FROM datacommune-prod-api:$tag
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
USER appuser
DOCKERFILE
fi

if [ -n "$setup_bundle" ]; then
  bundle_dir="$(cd "$(dirname "$setup_bundle")" && pwd -P)"
  DC_TAG="$tool_tag" dc run --rm --no-deps -T -v "$bundle_dir:/bundle:ro" -v "$PROD_DIR:/w" api \
    git clone -q --bare "/bundle/$(basename "$setup_bundle")" /w/repo.git </dev/null >&2
fi

[ -z "$(docker ps -q --filter name=dc-import- </dev/null)" ] || refuse "un import tourne déjà (conteneur dc-import-*)"
if [ -z "$resume" ] && [ -z "$replay" ] && [ -z "$setup_bundle" ]; then
  existing="$(find "$IMPORTS" -mindepth 2 -maxdepth 2 -name latest.json 2>/dev/null | head -1)"
  [ -z "$existing" ] || refuse "reçus déjà présents ($existing) : reprendre avec --resume <jeu>"
fi

DC_TAG="$tool_tag" dc run --rm --no-deps -T -v "$PROD_DIR/repo.git:/repo:ro" api \
  git -C /repo cat-file -e "$sha^{commit}" </dev/null >/dev/null 2>&1 ||
  refuse "repo_sha absent de $PROD_DIR/repo.git ($sha) : les reçus ne pourraient pas être exportés (README § 10)"
if [ -n "$setup_bundle" ]; then echo "REPO_OK $PROD_DIR/repo.git porte $sha"; exit 0; fi

# --- one dataset ---------------------------------------------------------------------------------
disk_used() { df -B1 --output=used / | tail -1 | tr -d ' '; }
peak() { awk 'BEGIN { m = "" } /^[0-9]+$/ { if (m == "" || $1 + 0 > m + 0) m = $1 } END { print m }' "$1"; }
json_num() { if [ -n "$1" ]; then printf '%s' "$1"; else printf '"%s"' "$NOT_MEASURED"; fi; }

import_one() {
  local ds="$1" dir="$IMPORTS/$1" s rc sampler max_before etl_id row run_id status duration log
  shift
  mkdir -p "$dir/logs"
  s="$(mktemp -d)"
  max_before="$(q "SELECT coalesce(max(id), 0) FROM etl_runs" </dev/null)"
  disk_used >"$s/baseline"; cp "$s/baseline" "$s/disk"; : >"$s/wal"; : >"$s/mem"
  (
    while :; do
      disk_used >>"$s/disk" 2>/dev/null || true
      dc exec -T db du -sb "$WAL_DIR" </dev/null 2>/dev/null | cut -f1 >>"$s/wal" || true
      docker exec "dc-import-$ds" cat /sys/fs/cgroup/memory.peak </dev/null >>"$s/mem" 2>/dev/null || true
      sleep "$INTERVAL"
    done
  ) &
  sampler=$!
  log="$dir/logs/run-$(date -u +%Y%m%dT%H%M%SZ).log"
  set +e
  dc run --rm -T --name "dc-import-$ds" api python -m run_etl "$ds" "$@" </dev/null 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  set -e
  kill "$sampler" 2>/dev/null || true; wait "$sampler" 2>/dev/null || true
  disk_used >>"$s/disk"

  etl_id="$(q "SELECT id FROM etl_runs WHERE dataset_name = '$ds' AND id > $max_before ORDER BY id DESC LIMIT 1" </dev/null)"
  if [ -z "$etl_id" ]; then
    echo "IMPORT $ds failed run_id=none run_etl_exit=$rc (aucune ligne etl_runs ; journal $log)"
    rm -rf "$s"; return 1
  fi
  row="$(q "SELECT id || '-' || to_char(started_at AT TIME ZONE 'UTC', 'YYYYMMDD\"T\"HH24MISS\"Z\"') || ' ' || status || ' ' || coalesce(round(total_duration_seconds::numeric, 1)::text, '') FROM etl_runs WHERE id = $etl_id" </dev/null)"
  read -r run_id status duration <<<"$row"
  mv "$log" "$dir/logs/$run_id.log"

  local disk_peak wal_peak mem_peak samples
  disk_peak="$(peak "$s/disk")"; wal_peak="$(peak "$s/wal")"; mem_peak="$(peak "$s/mem")"
  samples="$(grep -c . "$s/disk")"
  DC_TAG="$tool_tag" dc run --rm --no-deps -T -v "$IMPORTS:/imports" -v "$PROD_DIR/repo.git:/repo:ro" api \
    python -m scripts.export_import_manifest --dataset "$ds" --run-id "$run_id" --repo-sha "$sha" \
    --repo-dir /repo --out "/imports/$ds/$run_id.json" --pointer "/imports/$ds/latest.json" \
    --disk-peak-bytes "$disk_peak" --disk-peak-volume / --disk-peak-interval-s "$INTERVAL" \
    --disk-baseline-bytes "$(cat "$s/baseline")" --disk-samples "$samples" \
    --run-log "/imports/$ds/logs/$run_id.log" </dev/null ||
    { echo "IMPORT $ds failed run_id=$run_id (export du reçu en échec)"; rm -rf "$s"; return 1; }
  (
    set -o noclobber
    cat >"$dir/$run_id.mesures.json" <<EOF
{
  "dataset": "$ds",
  "run_id": "$run_id",
  "status": "$status",
  "run_etl_exit": $rc,
  "duration_s": $(json_num "$duration"),
  "sample_interval_s": $INTERVAL,
  "samples": $samples,
  "disk_baseline_bytes": $(json_num "$(cat "$s/baseline")"),
  "disk_peak_bytes": $(json_num "$disk_peak"),
  "disk_method": "df_used_root_sampled",
  "wal_dir_peak_bytes": $(json_num "$wal_peak"),
  "wal_method": "du_pg_wal_sampled",
  "memory_peak_bytes": $(json_num "$mem_peak"),
  "memory_method": "cgroup_v2_memory_peak_sampled"
}
EOF
  )
  rm -rf "$s"
  if [ "$rc" = 0 ] && [ "$status" = success ] && success_receipt "$ds"; then
    echo "IMPORT $ds success run_id=$run_id duration_s=$duration disk_peak=$disk_peak wal_dir_peak=${wal_peak:-none} memory_peak=${mem_peak:-none}"
    return 0
  fi
  echo "IMPORT $ds failed run_id=$run_id status=$status run_etl_exit=$rc (journal $dir/logs/$run_id.log)"
  return 1
}

for ds in "${selected[@]}"; do
  import_one "$ds" "${extra[@]}" || { echo "séquence arrêtée à $ds ; reprendre par --resume $ds" >&2; exit 1; }
done
echo "IMPORT_SEQUENCE_OK datasets=${#selected[@]} sha=$sha"
