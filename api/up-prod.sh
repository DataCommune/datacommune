#!/bin/bash
echo "DEPRECATED: ce script cible la stack legacy prod01 (docker-compose.prod.yml). Utiliser api/docs/deploy-runbook.md." >&2; exit 1
set -euo pipefail
cd "$(dirname "$0")"

# Production startup — uses .env.prod for variable interpolation
# Container environment set via environment: blocks in docker-compose.prod.yml

if [ ! -f .env.prod ]; then
  echo "ERROR: .env.prod not found. Copy .env.example to .env.prod and fill production values."
  exit 1
fi

docker compose \
  --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d "$@"
