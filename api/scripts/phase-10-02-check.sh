#!/bin/bash
# Phase 10-02 execution helper (run on LXC host).
#
# Runs migration, ETL validation, SQL checks, health checks, and backup sanity check.
# Writes a timestamped report file.
#
# Usage:
#   /opt/datacommune/scripts/phase-10-02-check.sh
#
# Optional env vars:
#   COMPOSE_DIR=/opt/datacommune
#   REPORT_DIR=/opt/datacommune/reports
#   API_KEY=<your key>   # enables /v1/datasets check
set -uo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/datacommune}"
REPORT_DIR="${REPORT_DIR:-${COMPOSE_DIR}/reports}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
REPORT_FILE="${REPORT_DIR}/phase-10-02_${TIMESTAMP}.log"
FAILURES=0

COMPOSE_CMD=(
  docker compose
  -f "${COMPOSE_DIR}/docker-compose.yml"
  -f "${COMPOSE_DIR}/docker-compose.prod.yml"
)

mkdir -p "${REPORT_DIR}"

# 17-01: mask API keys before anything reaches the report file
redact() {
  sed -E \
    -e "s/(X-API-Key:[[:space:]]*)[^[:space:]\"']+/\1***REDACTED***/g" \
    -e "s/dc_live_[A-Za-z0-9_-]+/dc_live_***REDACTED***/g"
}

log() {
  echo "[$(date -Iseconds)] $*" | redact | tee -a "${REPORT_FILE}"
}

run_cmd() {
  local cmd="$*"
  log "CMD: ${cmd}"
  if ! bash -c "${cmd}" 2>&1 | redact | tee -a "${REPORT_FILE}"; then
    log "FAIL: ${cmd}"
    FAILURES=$((FAILURES + 1))
  fi
}

log "=== Phase 10-02 check started ==="
log "Report file: ${REPORT_FILE}"

run_cmd "cd '${COMPOSE_DIR}' && ${COMPOSE_CMD[*]} up -d db api scheduler"
run_cmd "cd '${COMPOSE_DIR}' && ${COMPOSE_CMD[*]} ps"
run_cmd "cd '${COMPOSE_DIR}' && ${COMPOSE_CMD[*]} exec -T api alembic upgrade head"

run_cmd "cd '${COMPOSE_DIR}' && ${COMPOSE_CMD[*]} exec -T db psql -U datacommune -d datacommune -c \"select version();\""
run_cmd "cd '${COMPOSE_DIR}' && ${COMPOSE_CMD[*]} exec -T db psql -U datacommune -d datacommune -c \"select postgis_version();\""

run_cmd "cd '${COMPOSE_DIR}' && ${COMPOSE_CMD[*]} exec -T api python -m validate_etl"

run_cmd "cd '${COMPOSE_DIR}' && ${COMPOSE_CMD[*]} exec -T db psql -U datacommune -d datacommune -c \"
select dataset_name, status, row_count, last_updated_at
from dataset_metadata
order by dataset_name;\""

run_cmd "cd '${COMPOSE_DIR}' && ${COMPOSE_CMD[*]} exec -T db psql -U datacommune -d datacommune -c \"
select distinct on (dataset_name)
  dataset_name,
  status,
  started_at,
  completed_at,
  total_rows_loaded,
  error_message
from etl_runs
order by dataset_name, started_at desc;\""

run_cmd "curl -fsS http://localhost:8000/v1/health/live"
run_cmd "curl -i http://localhost:8000/v1/health"

if [[ -n "${API_KEY:-}" ]]; then
  run_cmd "curl -fsS -H 'X-API-Key: ${API_KEY}' http://localhost:8000/v1/datasets | head"
else
  log "SKIP: /v1/datasets check (API_KEY not set)"
fi

run_cmd "${COMPOSE_DIR}/scripts/backup-pg.sh"
run_cmd "ls -lh /var/backups/datacommune/pg | tail -n 5"

if [[ "${FAILURES}" -gt 0 ]]; then
  log "=== Phase 10-02 check completed with ${FAILURES} FAILURE(S) ==="
else
  log "=== Phase 10-02 check completed — all OK ==="
fi
log "Report saved to ${REPORT_FILE}"
exit "${FAILURES}"