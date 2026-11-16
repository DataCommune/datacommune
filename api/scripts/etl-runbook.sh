#!/usr/bin/env bash
# ETL Production Runbook — Automated verification
# Checks 10 datasets: row count + freshness from dataset_metadata
#
# Usage: ./scripts/etl-runbook.sh [--host SSH_HOST] [--max-age DAYS]
# Exit:  0 = all OK, 1 = at least one FAIL or WARN
#
# Environment variables (override defaults):
#   ETL_SSH_HOST     SSH alias for prod (default: datacommune)
#   ETL_MAX_AGE_DAYS Max dataset age in days (default: 30)
#   ETL_DB_CONTAINER Docker container name (default: datacommune-api-db-1)
#   ETL_DB_USER      PostgreSQL user (default: datacommune)
#   ETL_DB_NAME      PostgreSQL database (default: datacommune)
set -euo pipefail

SSH_HOST="${ETL_SSH_HOST:-datacommune}"
MAX_AGE_DAYS="${ETL_MAX_AGE_DAYS:-30}"
DB_CONTAINER="${ETL_DB_CONTAINER:-datacommune-api-db-1}"
DB_USER="${ETL_DB_USER:-datacommune}"
DB_NAME="${ETL_DB_NAME:-datacommune}"

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --host) SSH_HOST="$2"; shift 2 ;;
        --max-age) MAX_AGE_DAYS="$2"; shift 2 ;;
        *) echo "Usage: $0 [--host SSH_HOST] [--max-age DAYS]"; exit 1 ;;
    esac
done

DATASETS="ban cadastre dvf dpe loyers batiments connectivite monuments scolaire transports"
OK=0
WARN=0
FAIL=0

echo "=== ETL Production Runbook — $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "Host: $SSH_HOST | Max age: ${MAX_AGE_DAYS}j | Container: $DB_CONTAINER"
echo ""

# Execute a psql query on the remote DB container
psql_query() {
    ssh "$SSH_HOST" "docker exec $DB_CONTAINER psql -U $DB_USER -d $DB_NAME -tAc \"$1\""
}

for dataset in $DATASETS; do
    rows=$(psql_query "SELECT COALESCE(row_count, 0) FROM dataset_metadata WHERE dataset_name = '$dataset'" 2>/dev/null || echo "0")
    age_days=$(psql_query "SELECT COALESCE(EXTRACT(DAY FROM NOW() - last_updated_at)::int, 9999) FROM dataset_metadata WHERE dataset_name = '$dataset'" 2>/dev/null || echo "9999")

    # Trim whitespace from query results
    rows=$(echo "$rows" | tr -d '[:space:]')
    age_days=$(echo "$age_days" | tr -d '[:space:]')

    # Default to 0 / 9999 if empty
    rows="${rows:-0}"
    age_days="${age_days:-9999}"

    if [[ -z "$rows" || "$rows" == "0" ]]; then
        printf "[FAIL] %-15s — ABSENT ou vide\n" "$dataset"
        ((FAIL++)) || true
    elif [[ "$age_days" -ge "$MAX_AGE_DAYS" ]]; then
        printf "[WARN] %-15s — %s rows — %sj (perime > %sj)\n" "$dataset" "$rows" "$age_days" "$MAX_AGE_DAYS"
        ((WARN++)) || true
    else
        printf "[OK]   %-15s — %s rows — %sj\n" "$dataset" "$rows" "$age_days"
        ((OK++)) || true
    fi
done

echo ""
echo "=== Bilan : ${OK} OK | ${WARN} WARN | ${FAIL} FAIL ==="

if [[ $FAIL -gt 0 || $WARN -gt 0 ]]; then
    echo "Action requise — voir docs/etl-runbook.md pour les commandes de relance"
    exit 1
fi
exit 0
