#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Development startup — uses .env.dev for variable interpolation
# Exposes ports, mounts volumes for hot reload

if [ ! -f .env.dev ]; then
  echo "ERROR: .env.dev not found. Copy .env.example to .env.dev and adjust for local dev."
  exit 1
fi

docker compose \
  --env-file .env.dev \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up "$@"
