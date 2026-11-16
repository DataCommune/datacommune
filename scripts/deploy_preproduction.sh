#!/usr/bin/env bash
# This runner and Compose project are dedicated to the preproduction container.
set -euo pipefail
[[ "${DEPLOYMENT_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid candidate SHA' >&2; exit 1; }
[[ "$(hostname)" == datacommune-preprod ]] || { echo 'Wrong deployment host' >&2; exit 1; }
export COMPOSE_PROJECT_NAME=datacommune-preprod
export ENV_FILE=/opt/datacommune-preprod/api/.env
test -f "$ENV_FILE"
grep -qx 'APP_ENVIRONMENT=preproduction' "$ENV_FILE"
grep -qx 'POSTGRES_DB=datacommune_preprod' "$ENV_FILE"
grep -qx 'AUTH_COOKIE_NAME=dev_access_token' "$ENV_FILE"
grep -qx 'AUTH_COOKIE_DOMAIN=' "$ENV_FILE"
grep -qx 'PREPROD_TUNNEL_ENABLED=\(true\|false\)' "$ENV_FILE"
available=$(df -Pk /var/lib/docker | awk 'NR==2 {print $4}')
[[ "$available" -ge 41943040 ]] || { echo 'Less than 40 GiB free' >&2; exit 1; }
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir/api"
compose=(docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.preprod.yml)
python3 ../scripts/preproduction_candidate.py --assert-current "$DEPLOYMENT_SHA"
# Interpolated config contains credentials: never print it.
"${compose[@]}" config --quiet
"${compose[@]}" build api
docker image inspect "datacommune-preprod-api:sha-${DEPLOYMENT_SHA}" >/dev/null
python3 ../scripts/preproduction_candidate.py --assert-current "$DEPLOYMENT_SHA"
"${compose[@]}" up -d db
"${compose[@]}" exec -T db sh -ceu 'test -r /run/secrets/postgres_password'
"${compose[@]}" run --rm --no-deps --entrypoint python api -c \
  'from pathlib import Path; paths=["database_url","jwt_secret_key","api_key_hmac_secret","admin_password"]; assert all((Path("/run/secrets") / p).read_bytes() for p in paths)'
"${compose[@]}" run --rm --no-deps api alembic upgrade head
# Persist only the candidate marker after successful migration.
python3 - "$ENV_FILE" "$DEPLOYMENT_SHA" <<'PY'
import os
import re
import sys
import tempfile
from pathlib import Path

path, sha = Path(sys.argv[1]), sys.argv[2]
content = path.read_text()
content = re.sub(r"(?m)^DEPLOYMENT_SHA=.*\n?", "", content)
fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".preprod-env-")
try:
    with os.fdopen(fd, "w") as handle:
        handle.write(content.rstrip() + "\nDEPLOYMENT_SHA=" + sha + "\n")
    os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
"${compose[@]}" up -d --no-deps api
"${compose[@]}" up -d --no-deps --force-recreate caddy
"${compose[@]}" exec -T caddy caddy validate --config /etc/caddy/Caddyfile
if grep -qx 'PREPROD_TUNNEL_ENABLED=true' "$ENV_FILE"; then
  test -s /opt/datacommune-preprod/private/cf_tunnel_token
  "${compose[@]}" --profile tunnel up -d --no-deps cloudflared
else
  "${compose[@]}" --profile tunnel stop cloudflared >/dev/null 2>&1 || true
fi
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 5 -H 'Host: dev-api.datacommune.fr' \
    http://127.0.0.1/v1/health/version | python3 -c '
import json, os, sys
p = json.load(sys.stdin)
assert p["environment"] == "preproduction"
assert p["deployment_sha"] == os.environ["DEPLOYMENT_SHA"]
assert p["schema_actual"] and p["schema_actual"] == p["schema_expected"]
'; then
    echo "Preproduction API and migration head verified for $DEPLOYMENT_SHA"
    exit 0
  fi
  sleep 2
done
echo 'Preproduction health check failed; candidate is not qualified' >&2
exit 1
