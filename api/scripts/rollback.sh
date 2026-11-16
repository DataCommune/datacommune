#!/bin/bash
echo "DEPRECATED: ce script cible la stack legacy prod01 (docker-compose.prod.yml). Utiliser api/docs/deploy-runbook.md." >&2; exit 1
# Rollback script -- rolls back api + scheduler to a previous image tag.
#
# Usage:
#   scripts/rollback.sh              # rolls back to .last-deploy-tag
#   scripts/rollback.sh abc1234      # rolls back to specific tag
#
# NOTE: Does NOT rollback Alembic migrations (too risky — do manually).
set -euo pipefail

COMPOSE_DIR="/opt/datacommune"
COMPOSE_CMD=(
  docker compose
  -f "${COMPOSE_DIR}/docker-compose.yml"
  -f "${COMPOSE_DIR}/docker-compose.prod.yml"
)
TAG_FILE="${COMPOSE_DIR}/.last-deploy-tag"

# Determine target tag
if [ -n "${1:-}" ]; then
  TARGET_TAG="$1"
elif [ -f "$TAG_FILE" ]; then
  TARGET_TAG=$(cat "$TAG_FILE")
else
  echo "[ERROR] No tag specified and no .last-deploy-tag found"
  echo "Usage: $0 [image-tag]"
  exit 1
fi

echo "[$(date -Iseconds)] Rolling back to datacommune-api:${TARGET_TAG}..."

cd "${COMPOSE_DIR}"

# Re-tag the target image as :latest so compose uses it
docker tag "datacommune-api:${TARGET_TAG}" datacommune-api:latest

echo "[$(date -Iseconds)] Restarting API and scheduler..."
"${COMPOSE_CMD[@]}" up -d --no-deps api scheduler

echo "[$(date -Iseconds)] Waiting for API to be healthy..."
for i in 1 2 3; do
  sleep 5
  if curl -sf http://localhost:80/v1/health/live > /dev/null 2>&1; then
    echo "[OK] Health check passed (attempt $i)"
    break
  fi
  echo "[WARN] Health check attempt $i failed"
  if [ "$i" -eq 3 ]; then
    echo "[ERROR] Health check failed after 3 attempts — rollback may have failed"
    exit 1
  fi
done

echo "[OK] Rolled back to datacommune-api:${TARGET_TAG} at $(date -Iseconds)"
echo "[WARN] Alembic migrations were NOT rolled back — check manually if needed"
