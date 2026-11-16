#!/bin/bash
echo "DEPRECATED: ce script cible la stack legacy prod01 (docker-compose.prod.yml). Utiliser api/docs/deploy-runbook.md." >&2; exit 1
# Full deployment script: rebrand LXC (bddpublique → datacommune) + deploy v1.1
#
# Executed from Windows (Git Bash) — connects to LXC 275 via SSH.
#
# Usage:
#   bash scripts/deploy-full.sh                  # standard deploy
#   bash scripts/deploy-full.sh --dry-run        # preview commands only
#   bash scripts/deploy-full.sh --with-etl       # deploy + run all 10 ETL pipelines
#   bash scripts/deploy-full.sh --skip-preflight # skip local lint/tests
#   bash scripts/deploy-full.sh --yes            # skip interactive confirmations
set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────
LXC_HOST="${LXC_HOST:?set LXC_HOST=root@<address of the LXC> (kept out of the repository)}"
LXC_OLD_DIR="/opt/bddpublique"
LXC_NEW_DIR="/opt/datacommune"
OLD_NAME="bddpublique"
NEW_NAME="datacommune"
BACKUP_OLD_DIR="/var/backups/bddpublique/pg"
BACKUP_NEW_DIR="/var/backups/datacommune/pg"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ── Colors ─────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Flags ──────────────────────────────────────────────────────────────────
DRY_RUN=false
WITH_ETL=false
SKIP_PREFLIGHT=false
AUTO_YES=false

for arg in "$@"; do
  case "$arg" in
    --dry-run)       DRY_RUN=true ;;
    --with-etl)      WITH_ETL=true ;;
    --skip-preflight) SKIP_PREFLIGHT=true ;;
    --yes)           AUTO_YES=true ;;
    *) echo -e "${RED}Unknown flag: $arg${NC}"; exit 1 ;;
  esac
done

# ── Resolve paths ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Helpers ────────────────────────────────────────────────────────────────
step_num=0
step() {
  step_num=$((step_num + 1))
  echo ""
  echo -e "${CYAN}[$step_num] $1${NC}"
}

ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; exit 1; }
info() { echo -e "  $1"; }

run_cmd() {
  if $DRY_RUN; then
    echo -e "  ${YELLOW}[DRY-RUN]${NC} $*"
  else
    "$@"
  fi
}

run_ssh() {
  if $DRY_RUN; then
    echo -e "  ${YELLOW}[DRY-RUN SSH]${NC}"
    echo "$1" | sed 's/^/    /'
  else
    ssh -o ConnectTimeout=10 "$LXC_HOST" bash -s <<CMD
$1
CMD
  fi
}

confirm() {
  if $AUTO_YES || $DRY_RUN; then
    return 0
  fi
  echo ""
  echo -e "${YELLOW}$1${NC}"
  read -rp "Continue? [y/N] " answer
  case "$answer" in
    [yY]|[yY][eE][sS]) return 0 ;;
    *) echo "Aborted."; exit 1 ;;
  esac
}

cleanup() {
  if [ $? -ne 0 ]; then
    echo ""
    echo -e "${RED}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║  DEPLOYMENT FAILED — rollback instructions below    ║${NC}"
    echo -e "${RED}╠══════════════════════════════════════════════════════╣${NC}"
    echo -e "${RED}║  1. SSH into LXC: ssh ${LXC_HOST}${NC}"
    echo -e "${RED}║  2. If DB was renamed:                              ║${NC}"
    echo -e "${RED}║     docker compose exec db psql -U postgres -c \\   ║${NC}"
    echo -e "${RED}║       \"ALTER DATABASE datacommune RENAME TO bddpublique;\"${NC}"
    echo -e "${RED}║  3. If volumes were renamed, reverse the mv:        ║${NC}"
    echo -e "${RED}║     systemctl stop docker                           ║${NC}"
    echo -e "${RED}║     cd /var/lib/docker/volumes/                     ║${NC}"
    echo -e "${RED}║     mv datacommune_* → bddpublique_*               ║${NC}"
    echo -e "${RED}║     systemctl start docker                          ║${NC}"
    echo -e "${RED}║  4. If dir was renamed:                             ║${NC}"
    echo -e "${RED}║     mv /opt/datacommune /opt/bddpublique            ║${NC}"
    echo -e "${RED}║  5. Restore from backup:                            ║${NC}"
    echo -e "${RED}║     pre_rebrand_${TIMESTAMP}.dump                   ║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════════════╝${NC}"
  fi
}
trap cleanup EXIT

echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  DataCommune — Full Deployment (rebrand + v1.1)     ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${NC}"
if $DRY_RUN; then
  echo -e "${YELLOW}  >> DRY-RUN MODE — no changes will be made <<${NC}"
fi

# ══════════════════════════════════════════════════════════════════════════
# PHASE 1: Pre-flight (local)
# ══════════════════════════════════════════════════════════════════════════

step "Pre-flight checks (local)"

if ! $SKIP_PREFLIGHT; then
  info "Running ruff lint..."
  run_cmd uv run ruff check "${REPO_ROOT}/backend/"
  ok "Lint passed"

  info "Running ruff format check..."
  run_cmd uv run ruff format --check "${REPO_ROOT}/backend/"
  ok "Format check passed"

  info "Running tests..."
  run_cmd uv run pytest "${REPO_ROOT}/backend/tests/" -v --tb=short
  ok "Tests passed"
else
  warn "Preflight skipped (--skip-preflight)"
fi

# Git status check
if ! $DRY_RUN; then
  cd "$REPO_ROOT"
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    warn "Git working directory is dirty — consider committing first"
  else
    ok "Git working directory clean"
  fi
fi

# SSH connectivity
info "Testing SSH to ${LXC_HOST}..."
if $DRY_RUN; then
  echo -e "  ${YELLOW}[DRY-RUN]${NC} ssh ${LXC_HOST} echo ok"
else
  if ssh -o ConnectTimeout=10 "$LXC_HOST" "echo ok" >/dev/null 2>&1; then
    ok "SSH connection to LXC"
  else
    fail "Cannot reach ${LXC_HOST} via SSH"
  fi
fi

# Detect current state on LXC
if ! $DRY_RUN; then
  LXC_STATE=$(ssh -o ConnectTimeout=10 "$LXC_HOST" bash -s <<'DETECT'
    if [ -d "/opt/bddpublique" ] && [ ! -d "/opt/datacommune" ]; then
      echo "old"
    elif [ -d "/opt/datacommune" ] && [ ! -d "/opt/bddpublique" ]; then
      echo "new"
    elif [ -d "/opt/bddpublique" ] && [ -d "/opt/datacommune" ]; then
      echo "both"
    else
      echo "none"
    fi
DETECT
  )
  info "LXC directory state: ${LXC_STATE}"
  if [ "$LXC_STATE" = "new" ]; then
    warn "LXC already uses /opt/datacommune — rebranding steps will be skipped"
  elif [ "$LXC_STATE" = "both" ]; then
    warn "Both /opt/bddpublique and /opt/datacommune exist — check manually"
  elif [ "$LXC_STATE" = "none" ]; then
    fail "Neither /opt/bddpublique nor /opt/datacommune found on LXC"
  fi
else
  LXC_STATE="old"
fi

# ══════════════════════════════════════════════════════════════════════════
# PHASE 2: Backup (LXC)
# ══════════════════════════════════════════════════════════════════════════

step "Backup PostgreSQL on LXC"

# Determine current DB name and compose dir based on LXC state
if [ "$LXC_STATE" = "old" ]; then
  CURRENT_DB="$OLD_NAME"
  CURRENT_USER="$OLD_NAME"
  CURRENT_DIR="$LXC_OLD_DIR"
  CURRENT_BACKUP_DIR="$BACKUP_OLD_DIR"
else
  CURRENT_DB="$NEW_NAME"
  CURRENT_USER="$NEW_NAME"
  CURRENT_DIR="$LXC_NEW_DIR"
  CURRENT_BACKUP_DIR="$BACKUP_NEW_DIR"
fi

BACKUP_FILE="${CURRENT_BACKUP_DIR}/pre_rebrand_${TIMESTAMP}.dump"

run_ssh "
set -euo pipefail
mkdir -p '${CURRENT_BACKUP_DIR}'
cd '${CURRENT_DIR}'

echo 'Creating backup: ${BACKUP_FILE}'
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T db pg_dump -Fc -U '${CURRENT_USER}' '${CURRENT_DB}' > '${BACKUP_FILE}'

SIZE=\$(du -h '${BACKUP_FILE}' | cut -f1)
if [ -s '${BACKUP_FILE}' ]; then
  echo \"OK: backup created (\${SIZE})\"
else
  echo 'FAIL: backup file is empty'
  exit 1
fi
"
ok "Backup completed: ${BACKUP_FILE}"

# ══════════════════════════════════════════════════════════════════════════
# PHASE 3: Rebranding (LXC)
# ══════════════════════════════════════════════════════════════════════════

if [ "$LXC_STATE" = "old" ]; then
  step "Rebranding LXC (bddpublique → datacommune)"

  confirm "This will rename DB, volumes, and directories. Backup is at ${BACKUP_FILE}."

  # 3.1 Stop api + scheduler (keep db alive for rename)
  info "3.1 Stopping api + scheduler..."
  run_ssh "
set -euo pipefail
cd '${LXC_OLD_DIR}'
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop api scheduler caddy 2>/dev/null || true
echo 'Stopped api, scheduler, caddy'
"
  ok "Services stopped (db still running)"

  # 3.2 Rename DB + user
  info "3.2 Renaming database and user..."
  run_ssh "
set -euo pipefail
cd '${LXC_OLD_DIR}'

# Check if already renamed
DB_EXISTS=\$(docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T db psql -U ${OLD_NAME} -d postgres -tAc \
  \"SELECT 1 FROM pg_database WHERE datname='${OLD_NAME}'\" 2>/dev/null || echo '')

if [ \"\$DB_EXISTS\" = '1' ]; then
  echo 'Terminating connections to ${OLD_NAME}...'
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec -T db psql -U ${OLD_NAME} -d postgres -c \
    \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${OLD_NAME}' AND pid<>pg_backend_pid();\"

  echo 'Renaming database...'
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec -T db psql -U ${OLD_NAME} -d postgres -c \
    \"ALTER DATABASE ${OLD_NAME} RENAME TO ${NEW_NAME};\"

  echo 'Renaming user...'
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec -T db psql -U ${OLD_NAME} -d postgres -c \
    \"ALTER USER ${OLD_NAME} RENAME TO ${NEW_NAME};\"

  echo 'DB + user renamed'
else
  echo 'DB already renamed (or not found as ${OLD_NAME}) — skipping'
fi
"
  ok "Database renamed"

  # 3.3 Stop all containers
  info "3.3 Stopping all containers..."
  run_ssh "
set -euo pipefail
cd '${LXC_OLD_DIR}'
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
echo 'All containers stopped'
"
  ok "All containers down"

  # 3.4-3.6 Stop Docker, rename volumes, start Docker
  info "3.4-3.6 Renaming Docker volumes..."
  run_ssh "
set -euo pipefail

systemctl stop docker
echo 'Docker stopped'

cd /var/lib/docker/volumes/

VOLUMES='pgdata etl_tmp caddy_data caddy_config'
for vol in \$VOLUMES; do
  OLD_VOL=\"${OLD_NAME}_\${vol}\"
  NEW_VOL=\"${NEW_NAME}_\${vol}\"
  if [ -d \"\${OLD_VOL}\" ] && [ ! -d \"\${NEW_VOL}\" ]; then
    mv \"\${OLD_VOL}\" \"\${NEW_VOL}\"
    echo \"Renamed: \${OLD_VOL} -> \${NEW_VOL}\"
  elif [ -d \"\${NEW_VOL}\" ]; then
    echo \"Already exists: \${NEW_VOL} — skipping\"
  else
    echo \"WARNING: \${OLD_VOL} not found\"
  fi
done

systemctl start docker
echo 'Docker started'
"
  ok "Volumes renamed"

  # 3.7 Rename project directory
  info "3.7 Renaming project directory..."
  run_ssh "
set -euo pipefail
if [ -d '${LXC_OLD_DIR}' ] && [ ! -d '${LXC_NEW_DIR}' ]; then
  mv '${LXC_OLD_DIR}' '${LXC_NEW_DIR}'
  echo 'Renamed: ${LXC_OLD_DIR} -> ${LXC_NEW_DIR}'
elif [ -d '${LXC_NEW_DIR}' ]; then
  echo 'Already renamed — skipping'
else
  echo 'ERROR: source directory not found'
  exit 1
fi
"
  ok "Directory renamed"

  # 3.8 Rename backup directory
  info "3.8 Renaming backup directory..."
  run_ssh "
set -euo pipefail
if [ -d '${BACKUP_OLD_DIR}' ] && [ ! -d '${BACKUP_NEW_DIR}' ]; then
  mkdir -p /var/backups/${NEW_NAME}
  mv '${BACKUP_OLD_DIR}' '${BACKUP_NEW_DIR}'
  echo 'Renamed: ${BACKUP_OLD_DIR} -> ${BACKUP_NEW_DIR}'
  # Clean empty parent if needed
  rmdir /var/backups/${OLD_NAME} 2>/dev/null || true
elif [ -d '${BACKUP_NEW_DIR}' ]; then
  echo 'Already renamed — skipping'
else
  echo 'WARNING: ${BACKUP_OLD_DIR} not found, creating ${BACKUP_NEW_DIR}'
  mkdir -p '${BACKUP_NEW_DIR}'
fi
"
  ok "Backup directory renamed"

  # 3.9 Update hostname
  info "3.9 Updating hostname..."
  run_ssh "
set -euo pipefail
CURRENT_HOSTNAME=\$(hostname)
if [ \"\$CURRENT_HOSTNAME\" != '${NEW_NAME}' ]; then
  hostnamectl set-hostname '${NEW_NAME}'
  echo \"Hostname changed: \${CURRENT_HOSTNAME} -> ${NEW_NAME}\"
else
  echo 'Hostname already set — skipping'
fi
"
  ok "Hostname updated"

  # 3.10 Update .env on LXC
  info "3.10 Updating .env file..."
  run_ssh "
set -euo pipefail
ENV_FILE='${LXC_NEW_DIR}/.env'
if [ -f \"\$ENV_FILE\" ]; then
  if grep -q '${OLD_NAME}' \"\$ENV_FILE\"; then
    sed -i 's/${OLD_NAME}/${NEW_NAME}/g' \"\$ENV_FILE\"
    echo '.env updated (bddpublique -> datacommune)'
  else
    echo '.env already uses datacommune — skipping'
  fi
else
  echo 'WARNING: .env not found at ${LXC_NEW_DIR}/.env'
fi
"
  ok "Environment file updated"

else
  step "Rebranding (skipped — already datacommune)"
fi

# ══════════════════════════════════════════════════════════════════════════
# PHASE 4: Transfer code (local → LXC)
# ══════════════════════════════════════════════════════════════════════════

step "Transferring code to LXC"

TARBALL="/tmp/datacommune_deploy_${TIMESTAMP}.tar.gz"

info "Creating tarball..."
if $DRY_RUN; then
  echo -e "  ${YELLOW}[DRY-RUN]${NC} tar czf ${TARBALL} (excluding .git, .planning, .claude, .env, .venv, __pycache__)"
else
  cd "$REPO_ROOT"
  tar czf "$TARBALL" \
    --exclude='.git' \
    --exclude='.planning' \
    --exclude='.claude' \
    --exclude='.env' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.ruff_cache' \
    --exclude='node_modules' \
    --exclude='*.pyc' \
    .
  TAR_SIZE=$(du -h "$TARBALL" | cut -f1)
  ok "Tarball created: ${TARBALL} (${TAR_SIZE})"
fi

info "Uploading to LXC..."
run_cmd scp "$TARBALL" "${LXC_HOST}:/tmp/"
ok "Tarball uploaded"

info "Extracting on LXC..."
run_ssh "
set -euo pipefail
mkdir -p '${LXC_NEW_DIR}'

# Preserve .env before extraction
if [ -f '${LXC_NEW_DIR}/.env' ]; then
  cp '${LXC_NEW_DIR}/.env' '/tmp/.env.datacommune.bak'
  echo 'Preserved existing .env'
fi

tar xzf '/tmp/$(basename "$TARBALL")' -C '${LXC_NEW_DIR}'
echo 'Code extracted'

# Restore .env
if [ -f '/tmp/.env.datacommune.bak' ]; then
  cp '/tmp/.env.datacommune.bak' '${LXC_NEW_DIR}/.env'
  echo 'Restored .env'
fi

# Fix CRLF (Windows → Linux)
find '${LXC_NEW_DIR}' -type f \( \
  -name '*.sh' -o -name '*.py' -o -name '*.yml' -o -name '*.yaml' \
  -o -name '*.toml' -o -name '*.cfg' -o -name '*.ini' -o -name '*.conf' \
  -o -name '*.json' -o -name '*.md' -o -name '*.txt' -o -name '*.env*' \
  -o -name 'Caddyfile' -o -name 'Dockerfile' -o -name '*.lock' \
\) -exec sed -i 's/\r\$//' {} +
echo 'CRLF fixed'

chmod +x '${LXC_NEW_DIR}/scripts/'*.sh
echo 'Scripts made executable'

# Cleanup
rm -f '/tmp/$(basename "$TARBALL")'
"
ok "Code deployed to ${LXC_NEW_DIR}"

# Cleanup local tarball
if ! $DRY_RUN; then
  rm -f "$TARBALL"
fi

# ══════════════════════════════════════════════════════════════════════════
# PHASE 5: Build & Deploy (LXC)
# ══════════════════════════════════════════════════════════════════════════

step "Building and deploying on LXC"

info "Building Docker image..."
run_ssh "
set -euo pipefail
cd '${LXC_NEW_DIR}'
docker build -f docker/Dockerfile -t datacommune-api:latest backend/
echo 'Image built'
"
ok "Docker image built"

info "Starting services..."
run_ssh "
set -euo pipefail
cd '${LXC_NEW_DIR}'
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
echo 'Services started'
"
ok "Services started"

info "Waiting for API to be healthy..."
if ! $DRY_RUN; then
  HEALTHY=false
  for i in $(seq 1 12); do
    sleep 5
    STATUS=$(ssh -o ConnectTimeout=10 "$LXC_HOST" \
      "cd '${LXC_NEW_DIR}' && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api python -c \"import httpx; r=httpx.get('http://localhost:8000/v1/health/live'); print('up' if r.status_code==200 else 'down')\" 2>/dev/null || echo 'down'")
    if [ "$STATUS" = "up" ]; then
      HEALTHY=true
      break
    fi
    info "  Attempt $i/12 — not ready yet..."
  done
  if $HEALTHY; then
    ok "API is healthy"
  else
    fail "API did not become healthy after 60s"
  fi
else
  echo -e "  ${YELLOW}[DRY-RUN]${NC} Would wait up to 60s for health check"
fi

info "Running Alembic migrations..."
run_ssh "
set -euo pipefail
cd '${LXC_NEW_DIR}'
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T api alembic upgrade head
echo 'Migrations complete'
"
ok "Migrations applied"

info "Pruning old images..."
run_ssh "docker image prune -f"
ok "Image prune done"

# ══════════════════════════════════════════════════════════════════════════
# PHASE 6: Verify (LXC)
# ══════════════════════════════════════════════════════════════════════════

step "Verification"

run_ssh "
set -euo pipefail
cd '${LXC_NEW_DIR}'
COMPOSE='docker compose -f docker-compose.yml -f docker-compose.prod.yml'

echo '--- Container status ---'
\$COMPOSE ps

echo ''
echo '--- Health checks ---'
HTTP_LIVE=\$(\$COMPOSE exec -T api python -c "import httpx; print(httpx.get('http://localhost:8000/v1/health/live').status_code)" 2>/dev/null || echo 'FAIL')
HTTP_HEALTH=\$(\$COMPOSE exec -T api python -c "import httpx; print(httpx.get('http://localhost:8000/v1/health').status_code)" 2>/dev/null || echo 'FAIL')
echo \"  /v1/health/live  -> HTTP \${HTTP_LIVE}\"
echo \"  /v1/health       -> HTTP \${HTTP_HEALTH}\"

echo ''
echo '--- Database version ---'
\$COMPOSE exec -T db psql -U ${NEW_NAME} -d ${NEW_NAME} -c 'SELECT version();' 2>/dev/null || echo 'DB query failed'

echo ''
echo '--- Container names ---'
docker ps --format '{{.Names}}' | grep -E '(datacommune|bddpublique)' || echo 'No matching containers'

echo ''
echo '--- Volume names ---'
docker volume ls --format '{{.Name}}' | grep -E '(datacommune|bddpublique)' || echo 'No matching volumes'

echo ''
echo '--- Alembic current ---'
\$COMPOSE exec -T api alembic current 2>/dev/null || echo 'Alembic check failed'
"
ok "Verification complete"

# ══════════════════════════════════════════════════════════════════════════
# PHASE 7: Setup cron (LXC)
# ══════════════════════════════════════════════════════════════════════════

step "Configuring cron backup"

run_ssh "
set -euo pipefail

# Remove old bddpublique cron entries
crontab -l 2>/dev/null | grep -v '${OLD_NAME}' > /tmp/crontab.new || true

# Check if datacommune cron already present
if grep -q '${NEW_NAME}' /tmp/crontab.new 2>/dev/null; then
  echo 'Cron for datacommune already exists — skipping'
else
  echo '0 3 * * * ${LXC_NEW_DIR}/scripts/backup-pg.sh >> /var/log/datacommune-backup.log 2>&1' >> /tmp/crontab.new
  echo 'Added backup cron job'
fi

crontab /tmp/crontab.new
rm -f /tmp/crontab.new

echo 'Current crontab:'
crontab -l
"
ok "Cron configured"

# ══════════════════════════════════════════════════════════════════════════
# PHASE 8: ETL (optional)
# ══════════════════════════════════════════════════════════════════════════

if $WITH_ETL; then
  step "Running ETL pipelines (10 datasets)"
  warn "This will take several hours (~570M rows)"

  confirm "Run all 10 ETL pipelines now?"

  run_ssh "
set -euo pipefail
cd '${LXC_NEW_DIR}'
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T api python /app/run_etl.py
echo 'ETL complete'
"

  info "Validating ETL results..."
  run_ssh "
set -euo pipefail
cd '${LXC_NEW_DIR}'
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T api python -m validate_etl
"
  ok "ETL pipelines completed and validated"
else
  step "ETL (skipped — use --with-etl to run)"
  info "To run ETL manually later:"
  info "  ssh ${LXC_HOST}"
  info "  cd ${LXC_NEW_DIR}"
  info "  docker compose -f docker-compose.yml -f docker-compose.prod.yml \\"
  info "    exec -T api python /app/run_etl.py"
fi

# ══════════════════════════════════════════════════════════════════════════
# Done
# ══════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Deployment complete!                                ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  LXC:    ${LXC_HOST}                    ║${NC}"
echo -e "${GREEN}║  Dir:    ${LXC_NEW_DIR}                       ║${NC}"
echo -e "${GREEN}║  HTTPS:  https://${LXC_HOST#*@}                       ║${NC}"
echo -e "${GREEN}║  Health: https://${LXC_HOST#*@}/v1/health             ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"

exit 0
