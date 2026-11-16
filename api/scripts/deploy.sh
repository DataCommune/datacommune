#!/bin/bash
echo "DEPRECATED: ce script cible la stack legacy prod01 (docker-compose.prod.yml). Utiliser api/docs/deploy-runbook.md." >&2; exit 1
# Deploy script -- executed by CI/CD pipeline via SSH on prod01.
#
# The CI/CD pipeline builds the image, pushes to ghcr.io, then pulls it
# on prod01 before calling this script to restart services.
#
# This script:
#   1. Restarts api + scheduler services (zero-downtime for PostgreSQL)
#   2. (Alembic migrations run separately by CI/CD pipeline)
#   3. Prunes dangling images to save disk space
#
# Usage (manual): /opt/datacommune/scripts/deploy.sh
# Usage (CI): Called by SSH in .github/workflows/cd.yml deploy job
set -euo pipefail

COMPOSE_DIR="/opt/datacommune"
COMPOSE_CMD=(
  docker compose
  -f "${COMPOSE_DIR}/docker-compose.yml"
  -f "${COMPOSE_DIR}/docker-compose.prod.yml"
)

cd "${COMPOSE_DIR}"

# Save current image tag for rollback
CURRENT_TAG=$(docker inspect --format='{{index .RepoTags 0}}' datacommune-api:latest 2>/dev/null | cut -d: -f2 || echo "unknown")
echo "$CURRENT_TAG" > "${COMPOSE_DIR}/.last-deploy-tag"
echo "[$(date -Iseconds)] Saved previous tag: ${CURRENT_TAG}"

echo "[$(date -Iseconds)] Restarting API and scheduler services..."
"${COMPOSE_CMD[@]}" up -d api scheduler

echo "[$(date -Iseconds)] Reloading Caddy configuration..."
"${COMPOSE_CMD[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile || echo "[WARN] Caddy reload failed"

echo "[$(date -Iseconds)] Waiting for API to be healthy..."
for i in 1 2 3; do
  sleep 5
  if curl -sf http://localhost:80/v1/health/live > /dev/null 2>&1; then
    echo "[OK] Health check passed (attempt $i)"
    break
  fi
  echo "[WARN] Health check attempt $i failed"
  if [ "$i" -eq 3 ]; then
    echo "[ERROR] Health check failed after 3 attempts"
    echo "[HINT] Run scripts/rollback.sh to revert to previous version"
    exit 1
  fi
done

# NOTE: Alembic migrations are NOT run here — they are an explicit step
# in the CI/CD pipeline (cd.yml). For manual deploys, run:
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api alembic upgrade head

echo "[$(date -Iseconds)] Pruning dangling images..."
docker image prune -f

echo "[OK] Deployed at $(date -Iseconds)"
