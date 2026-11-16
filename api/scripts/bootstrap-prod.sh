#!/bin/bash
# Bootstrap a fresh production server from zero to fully loaded.
#
# Prerequisites:
#   - Docker + Docker Compose installed
#   - Repo cloned at /opt/datacommune/api
#   - .env file in place
#
# Usage (run ON the server):
#   bash scripts/bootstrap-prod.sh              # Full setup: migrations + all ETL
#   bash scripts/bootstrap-prod.sh --etl-only   # Skip migrations, run ETL only
#   bash scripts/bootstrap-prod.sh --small-only  # Migrations + small datasets only (quick validation)
#   bash scripts/bootstrap-prod.sh --dry-run     # Preview without DB writes
set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
CONTAINER_API="api-api-1"
CONTAINER_DB="api-db-1"
LOG_FILE="/var/log/datacommune-bootstrap.log"

# Small datasets (< 1M rows, < 5min each) — for quick validation
SMALL_DATASETS="monuments loyers transports scolaire zonage_abc fiscalite encadrement_loyers revenus demographie vacance copropriete permis antennes"
# Medium datasets (1M-30M rows, 5-60min each)
MEDIUM_DATASETS="equipements majic_pm dvf ban dpe"
# Large datasets (30M+ rows, 1h+ each)
LARGE_DATASETS="cadastre batiments connectivite"

ALL_DATASETS="$SMALL_DATASETS $MEDIUM_DATASETS $LARGE_DATASETS"

# ── Colors ─────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Flags ──────────────────────────────────────────────────────────────────
ETL_ONLY=false
SMALL_ONLY=false
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --etl-only)   ETL_ONLY=true ;;
    --small-only) SMALL_ONLY=true ;;
    --dry-run)    DRY_RUN=true ;;
    *) echo -e "${RED}Unknown flag: $arg${NC}"; exit 1 ;;
  esac
done

# ── Helpers ────────────────────────────────────────────────────────────────
step_num=0
step() {
  step_num=$((step_num + 1))
  echo ""
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${CYAN}  [$step_num] $1${NC}"
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
info() { echo -e "  $1"; }

ts() { date '+%H:%M:%S'; }

format_duration() {
  local seconds=$1
  local h=$((seconds / 3600))
  local m=$(( (seconds % 3600) / 60 ))
  local s=$((seconds % 60))
  if [ $h -gt 0 ]; then
    printf "%dh%02dm%02ds" $h $m $s
  elif [ $m -gt 0 ]; then
    printf "%dm%02ds" $m $s
  else
    printf "%ds" $s
  fi
}

wait_healthy() {
  local container=$1
  local max_wait=${2:-60}
  local elapsed=0
  while [ $elapsed -lt $max_wait ]; do
    if docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null | grep -q "healthy"; then
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  return 1
}

# ── Pre-flight ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║  DataCommune — Production Bootstrap                     ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"

if $DRY_RUN; then
  warn "DRY-RUN MODE — ETL will download and validate only (no DB writes)"
fi

cd "$REPO_ROOT"

# Check .env exists
if [ ! -f .env ]; then
  fail ".env file not found at $REPO_ROOT/.env"
fi

# Check Docker
if ! docker info > /dev/null 2>&1; then
  fail "Docker is not running"
fi

# Check disk space
FREE_GB=$(df -BG --output=avail /var/lib/docker | tail -1 | tr -d ' G')
info "Disk space: ${FREE_GB} GB free"
if [ "$FREE_GB" -lt 50 ]; then
  fail "Not enough disk space (need 50 GB+, have ${FREE_GB} GB)"
fi
ok "Pre-flight passed"

# ══════════════════════════════════════════════════════════════════════════
# PHASE 1: Ensure containers are running
# ══════════════════════════════════════════════════════════════════════════

if ! $ETL_ONLY; then
  step "Starting containers"

  $COMPOSE up -d 2>&1 | tail -5
  ok "Containers launched"

  info "Waiting for database to be healthy..."
  if wait_healthy "$CONTAINER_DB" 60; then
    ok "Database healthy"
  else
    fail "Database did not become healthy in 60s"
  fi

  info "Waiting for API to be healthy..."
  if wait_healthy "$CONTAINER_API" 30; then
    ok "API healthy"
  else
    warn "API not healthy yet (may need migrations)"
  fi

  # ════════════════════════════════════════════════════════════════════════
  # PHASE 2: Run Alembic migrations
  # ════════════════════════════════════════════════════════════════════════

  step "Running Alembic migrations"

  CURRENT=$(docker exec "$CONTAINER_API" alembic current 2>/dev/null | grep -oE '[a-f0-9]{12}' | head -1 || echo "none")
  info "Current migration: ${CURRENT:-none}"

  docker exec "$CONTAINER_API" alembic upgrade head 2>&1
  ok "Migrations applied"

  NEW_HEAD=$(docker exec "$CONTAINER_API" alembic current 2>/dev/null | grep -oE '[a-f0-9]{12}' | head -1 || echo "unknown")
  info "Migration head: $NEW_HEAD"

  # Verify key tables exist
  TABLE_COUNT=$(docker exec "$CONTAINER_DB" psql -U datacommune -d datacommune -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null || echo "0")
  info "Tables in public schema: $TABLE_COUNT"
  if [ "$TABLE_COUNT" -lt 10 ]; then
    fail "Expected 10+ tables after migrations, got $TABLE_COUNT"
  fi
  ok "Schema verified ($TABLE_COUNT tables)"
fi

# ══════════════════════════════════════════════════════════════════════════
# PHASE 3: Run ETL pipelines
# ══════════════════════════════════════════════════════════════════════════

if $SMALL_ONLY; then
  DATASETS="$SMALL_DATASETS"
  step "Running ETL — small datasets only (validation)"
else
  DATASETS="$ALL_DATASETS"
  step "Running ETL — all datasets"
fi

TOTAL_START=$(date +%s)
PASSED=0
FAILED=0
FAILED_LIST=""

DRY_FLAG=""
if $DRY_RUN; then
  DRY_FLAG="--dry-run"
fi

for dataset in $DATASETS; do
  DS_START=$(date +%s)
  echo ""
  echo -e "  ${BOLD}[$(ts)] Starting: $dataset${NC}"

  if docker exec "$CONTAINER_API" python /app/run_etl.py $DRY_FLAG "$dataset" >> "$LOG_FILE" 2>&1; then
    DS_END=$(date +%s)
    DS_DUR=$((DS_END - DS_START))

    # Get row count from DB
    ROW_COUNT=$(docker exec "$CONTAINER_DB" psql -U datacommune -d datacommune -tAc \
      "SELECT COALESCE(row_count, 0) FROM dataset_metadata WHERE dataset_name='$dataset';" 2>/dev/null || echo "?")

    ok "$dataset — ${ROW_COUNT} rows — $(format_duration $DS_DUR)"
    PASSED=$((PASSED + 1))
  else
    DS_END=$(date +%s)
    DS_DUR=$((DS_END - DS_START))
    echo -e "  ${RED}✗${NC} $dataset — FAILED after $(format_duration $DS_DUR)"
    # Show last 5 lines of error from log
    tail -5 "$LOG_FILE" 2>/dev/null | sed 's/^/    /'
    FAILED=$((FAILED + 1))
    FAILED_LIST="$FAILED_LIST $dataset"
  fi
done

TOTAL_END=$(date +%s)
TOTAL_DUR=$((TOTAL_END - TOTAL_START))

# ══════════════════════════════════════════════════════════════════════════
# PHASE 4: Final report
# ══════════════════════════════════════════════════════════════════════════

step "Results"

echo ""
echo -e "  ${BOLD}Dataset Status:${NC}"
docker exec "$CONTAINER_DB" psql -U datacommune -d datacommune -c \
  "SELECT dataset_name AS dataset,
          status,
          row_count AS rows,
          ROUND(last_import_duration_seconds::numeric, 1) AS duration_s,
          TO_CHAR(last_updated_at, 'YYYY-MM-DD HH24:MI') AS updated
   FROM dataset_metadata
   ORDER BY last_updated_at DESC NULLS LAST;" 2>/dev/null || warn "Could not query dataset_metadata"

echo ""
echo -e "  ──────────────────────────────────────"
echo -e "  ${GREEN}Passed:${NC}  $PASSED"
echo -e "  ${RED}Failed:${NC}  $FAILED"
if [ -n "$FAILED_LIST" ]; then
  echo -e "  ${RED}Failed datasets:${NC}$FAILED_LIST"
fi
echo -e "  ${BOLD}Total duration:${NC} $(format_duration $TOTAL_DUR)"
echo -e "  ${BOLD}Full log:${NC} $LOG_FILE"

# Health check
echo ""
info "Final health check..."
HTTP_CODE=$(docker exec "$CONTAINER_API" python -c "
import httpx
r = httpx.get('http://localhost:8000/v1/health')
print(r.status_code)
" 2>/dev/null || echo "FAIL")
info "/v1/health -> HTTP $HTTP_CODE"

echo ""
if [ $FAILED -eq 0 ]; then
  echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║  Bootstrap complete — all datasets loaded!               ║${NC}"
  echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
  exit 0
else
  echo -e "${YELLOW}╔══════════════════════════════════════════════════════════╗${NC}"
  echo -e "${YELLOW}║  Bootstrap complete with $FAILED failure(s)                     ║${NC}"
  echo -e "${YELLOW}║  Retry: bash scripts/bootstrap-prod.sh --etl-only       ║${NC}"
  echo -e "${YELLOW}║  Or retry specific: docker exec $CONTAINER_API \\        ║${NC}"
  echo -e "${YELLOW}║    python /app/run_etl.py <dataset_name>                ║${NC}"
  echo -e "${YELLOW}╚══════════════════════════════════════════════════════════╝${NC}"
  exit 1
fi
