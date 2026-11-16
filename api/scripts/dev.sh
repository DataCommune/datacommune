#!/bin/bash
# DataCommune — Local dev environment
#
# Opens SSH tunnel to prod DB, starts Docker Compose with hot reload.
# Ctrl+C to stop. Tunnel stays alive in background (reused on next run).
#
# First-time setup:
#   1. scp docker-compose.tunnel.yml datacommune:/opt/datacommune/
#   2. ssh datacommune "cd /opt/datacommune && docker compose -f docker-compose.yml \
#        -f docker-compose.prod.yml -f docker-compose.tunnel.yml up -d db"
#   3. Add PROD_DB_PASSWORD=<password> to .env
#
# Usage: ./scripts/dev.sh

set -euo pipefail
cd "$(dirname "$0")/.."

LOCAL_PORT=15432
SSH_HOST="datacommune"
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

# --- Check .env ---
if ! grep -q "PROD_DB_PASSWORD" .env 2>/dev/null; then
    echo -e "${RED}[dev]${NC} PROD_DB_PASSWORD missing in .env"
    echo "  Add: PROD_DB_PASSWORD=<your_prod_db_password>"
    exit 1
fi

# --- SSH tunnel ---
if netstat -an 2>/dev/null | grep -q ":${LOCAL_PORT}.*LISTEN"; then
    echo -e "${GREEN}[dev]${NC} SSH tunnel already running on port ${LOCAL_PORT}"
else
    echo -e "${GREEN}[dev]${NC} Opening SSH tunnel (localhost:${LOCAL_PORT} -> LXC prod DB)..."
    ssh -f -N -L ${LOCAL_PORT}:localhost:5432 ${SSH_HOST}
    echo -e "${GREEN}[dev]${NC} Tunnel open."
fi

# --- Docker Compose ---
echo -e "${GREEN}[dev]${NC} Starting local stack..."
echo -e "${GREEN}[dev]${NC} API direct:  http://localhost:8000"
echo -e "${GREEN}[dev]${NC} Via Caddy:   http://localhost (with CSP headers)"
echo ""

docker compose -f docker-compose.yml -f docker-compose.local.yml up --build
