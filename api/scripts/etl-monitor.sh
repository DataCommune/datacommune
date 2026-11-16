#!/bin/bash
# Live ETL monitoring dashboard — polls etl_runs + dataset_metadata every N seconds.
#
# Usage:
#   bash scripts/etl-monitor.sh                    # Local (run on the server)
#   bash scripts/etl-monitor.sh --remote prod01    # SSH to remote host
#   bash scripts/etl-monitor.sh --interval 5       # Poll every 5s (default: 10)
#   bash scripts/etl-monitor.sh --once             # Single snapshot, no loop
set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────
INTERVAL=10
REMOTE=""
ONCE=false
CONTAINER_DB="api-db-1"
DB_USER="datacommune"
DB_NAME="datacommune"

# All known datasets for gap detection
ALL_DATASETS="monuments loyers transports scolaire zonage_abc fiscalite encadrement_loyers revenus demographie vacance copropriete permis antennes equipements majic_pm dvf ban dpe cadastre batiments connectivite"

# ── Parse args ─────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote)    REMOTE="$2"; shift 2 ;;
    --interval)  INTERVAL="$2"; shift 2 ;;
    --once)      ONCE=true; shift ;;
    *) echo "Unknown: $1"; exit 1 ;;
  esac
done

# ── Colors ─────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
BLINK='\033[5m'
NC='\033[0m'

# ── Helpers ────────────────────────────────────────────────────────────────
run_psql() {
  local query="$1"
  if [ -n "$REMOTE" ]; then
    ssh -o ConnectTimeout=5 "$REMOTE" \
      "docker exec $CONTAINER_DB psql -U $DB_USER -d $DB_NAME -tA -F '|' -c \"$query\"" 2>/dev/null
  else
    docker exec "$CONTAINER_DB" psql -U "$DB_USER" -d "$DB_NAME" -tA -F '|' -c "$query" 2>/dev/null
  fi
}

format_rows() {
  local n=$1
  if [ "$n" -ge 1000000 ]; then
    printf "%.1fM" "$(echo "scale=1; $n / 1000000" | bc)"
  elif [ "$n" -ge 1000 ]; then
    printf "%.1fK" "$(echo "scale=1; $n / 1000" | bc)"
  else
    printf "%d" "$n"
  fi
}

format_duration() {
  local seconds=${1%.*}  # strip decimals
  seconds=${seconds:-0}
  local h=$((seconds / 3600))
  local m=$(( (seconds % 3600) / 60 ))
  local s=$((seconds % 60))
  if [ $h -gt 0 ]; then
    printf "%dh%02dm" $h $m
  elif [ $m -gt 0 ]; then
    printf "%dm%02ds" $m $s
  else
    printf "%ds" $s
  fi
}

# ── Main render ────────────────────────────────────────────────────────────
render() {
  clear

  local now
  now=$(date '+%Y-%m-%d %H:%M:%S')
  local target="${REMOTE:-localhost}"

  echo -e "${BOLD}╔══════════════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${BOLD}║  DataCommune ETL Monitor  ${DIM}$now  @$target${NC}"
  echo -e "${BOLD}╚══════════════════════════════════════════════════════════════════════╝${NC}"

  # ── Currently running pipelines ────────────────────────────────────────
  local running
  running=$(run_psql "
    SELECT dataset_name,
           TO_CHAR(started_at, 'HH24:MI:SS'),
           EXTRACT(EPOCH FROM NOW() - started_at)::int
    FROM etl_runs
    WHERE status = 'running'
    ORDER BY started_at ASC;
  " 2>/dev/null || echo "")

  echo ""
  if [ -n "$running" ]; then
    echo -e "  ${BLINK}${YELLOW}▶ RUNNING${NC}"
    echo -e "  ${DIM}%-20s %-10s %-10s${NC}" "Dataset" "Started" "Elapsed"
    echo -e "  ${DIM}────────────────────────────────────────────${NC}"
    while IFS='|' read -r ds started elapsed; do
      [ -z "$ds" ] && continue
      echo -e "  ${YELLOW}%-20s${NC} %-10s ${BOLD}%s${NC}" "$ds" "$started" "$(format_duration "$elapsed")"
    done <<< "$running"
  else
    echo -e "  ${DIM}No ETL currently running${NC}"
  fi

  # ── Recent completions (last 2 hours) ──────────────────────────────────
  local recent
  recent=$(run_psql "
    SELECT dataset_name,
           status,
           total_rows_loaded,
           total_duration_seconds,
           TO_CHAR(completed_at, 'HH24:MI:SS'),
           COALESCE(LEFT(error_message, 50), '')
    FROM etl_runs
    WHERE completed_at > NOW() - INTERVAL '2 hours'
    ORDER BY completed_at DESC
    LIMIT 15;
  " 2>/dev/null || echo "")

  echo ""
  echo -e "  ${BOLD}Recent (2h)${NC}"
  printf "  ${DIM}%-20s %-8s %10s %8s %-8s %s${NC}\n" "Dataset" "Status" "Rows" "Duration" "Done" "Error"
  echo -e "  ${DIM}──────────────────────────────────────────────────────────────────${NC}"

  if [ -n "$recent" ]; then
    while IFS='|' read -r ds status rows dur completed err; do
      [ -z "$ds" ] && continue
      rows=${rows:-0}
      dur=${dur:-0}

      local color="$NC"
      local icon="?"
      if [ "$status" = "success" ]; then
        color="$GREEN"
        icon="✓"
      elif [ "$status" = "failed" ]; then
        color="$RED"
        icon="✗"
      fi

      printf "  ${color}%-2s %-18s %-8s %10s %8s %-8s %s${NC}\n" \
        "$icon" "$ds" "$status" "$(format_rows "$rows")" "$(format_duration "$dur")" "$completed" "$err"
    done <<< "$recent"
  else
    echo -e "  ${DIM}No recent ETL runs${NC}"
  fi

  # ── Dataset inventory ──────────────────────────────────────────────────
  local metadata
  metadata=$(run_psql "
    SELECT dataset_name,
           status,
           row_count,
           TO_CHAR(last_updated_at, 'YYYY-MM-DD HH24:MI')
    FROM dataset_metadata
    ORDER BY dataset_name;
  " 2>/dev/null || echo "")

  echo ""
  echo -e "  ${BOLD}Dataset Inventory${NC}"
  printf "  ${DIM}%-22s %-12s %12s %-18s${NC}\n" "Dataset" "Status" "Rows" "Last Updated"
  echo -e "  ${DIM}──────────────────────────────────────────────────────────────────${NC}"

  local loaded_count=0
  local loaded_datasets=""

  if [ -n "$metadata" ]; then
    while IFS='|' read -r ds status rows updated; do
      [ -z "$ds" ] && continue
      rows=${rows:-0}

      local color="$GREEN"
      if [ "$status" != "disponible" ]; then
        color="$YELLOW"
      fi
      if [ "$rows" -eq 0 ] 2>/dev/null; then
        color="$RED"
      fi

      printf "  ${color}%-22s %-12s %12s %-18s${NC}\n" "$ds" "$status" "$(format_rows "$rows")" "${updated:---}"
      loaded_count=$((loaded_count + 1))
      loaded_datasets="$loaded_datasets $ds"
    done <<< "$metadata"
  fi

  # Show missing datasets
  local missing=""
  for ds in $ALL_DATASETS; do
    if ! echo "$loaded_datasets" | grep -qw "$ds"; then
      missing="$missing $ds"
    fi
  done

  if [ -n "$missing" ]; then
    for ds in $missing; do
      printf "  ${RED}%-22s %-12s %12s %-18s${NC}\n" "$ds" "missing" "0" "--"
    done
  fi

  # ── Summary bar ────────────────────────────────────────────────────────
  local total_ds
  total_ds=$(echo $ALL_DATASETS | wc -w)
  local missing_count
  missing_count=$(echo $missing | wc -w)
  missing_count=${missing_count:-0}

  echo ""
  local pct=0
  if [ "$total_ds" -gt 0 ]; then
    pct=$(( (total_ds - missing_count) * 100 / total_ds ))
  fi

  # Progress bar
  local bar_width=40
  local filled=$(( pct * bar_width / 100 ))
  local empty=$(( bar_width - filled ))
  local bar=""
  for ((i=0; i<filled; i++)); do bar="${bar}█"; done
  for ((i=0; i<empty; i++)); do bar="${bar}░"; done

  echo -e "  ${BOLD}Progress:${NC} [${GREEN}${bar}${NC}] ${pct}% (${loaded_count}/${total_ds} datasets)"

  # ── Disk space ─────────────────────────────────────────────────────────
  local disk
  if [ -n "$REMOTE" ]; then
    disk=$(ssh -o ConnectTimeout=5 "$REMOTE" "df -h / --output=avail,pcent | tail -1" 2>/dev/null || echo "? ?")
  else
    disk=$(df -h / --output=avail,pcent | tail -1 2>/dev/null || echo "? ?")
  fi
  echo -e "  ${BOLD}Disk:${NC} $disk free"

  echo ""
  if ! $ONCE; then
    echo -e "  ${DIM}Refreshing every ${INTERVAL}s — Ctrl+C to exit${NC}"
  fi
}

# ── Loop ───────────────────────────────────────────────────────────────────
if $ONCE; then
  render
else
  trap 'echo ""; echo "Monitor stopped."; exit 0' INT
  while true; do
    render
    sleep "$INTERVAL"
  done
fi
