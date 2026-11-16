#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runner="$repo_dir/scripts/deploy_preproduction.sh"
tmp=$(mktemp -d)
secret_project="dc-preprod-secret-$$"
cleanup() {
  if test -f "$tmp/secret-compose.yml"; then
    docker compose -p "$secret_project" -f "$tmp/secret-compose.yml" down \
      >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp"
}
trap cleanup EXIT

if DEPLOYMENT_SHA=invalid bash "$runner" >"$tmp/out" 2>"$tmp/err"; then
  echo "invalid SHA was accepted" >&2
  exit 1
fi
grep -q "Invalid candidate SHA" "$tmp/err"

cat >"$tmp/hostname" <<'STUB'
#!/usr/bin/env bash
echo production-host
STUB
chmod +x "$tmp/hostname"
if PATH="$tmp:$PATH" DEPLOYMENT_SHA=0123456789abcdef0123456789abcdef01234567 \
  bash "$runner" >"$tmp/out" 2>"$tmp/err"; then
  echo "wrong hostname was accepted" >&2
  exit 1
fi
grep -q "Wrong deployment host" "$tmp/err"

export POSTGRES_DB=datacommune_preprod
export POSTGRES_USER=datacommune_preprod
export PREPROD_SECRETS_DIR=/opt/datacommune-preprod/private
export DEPLOYMENT_SHA=0123456789abcdef0123456789abcdef01234567
config=$(docker compose -p datacommune-preprod \
  -f "$repo_dir/api/docker-compose.yml" \
  -f "$repo_dir/api/docker-compose.preprod.yml" config --format json)
profiles=$(docker compose -p datacommune-preprod \
  -f "$repo_dir/api/docker-compose.yml" \
  -f "$repo_dir/api/docker-compose.preprod.yml" config --profiles)
# The scheduler service and its "disabled-in-preproduction" profile went with
# f61fb0e (systemd timers replace it): without a profile, preproduction runs
# exactly api, caddy and db; cloudflared only under the "tunnel" profile.
test "$profiles" = tunnel
python3 -c '
import json, sys
c = json.load(sys.stdin)
assert sorted(c["services"]) == ["api", "caddy", "db"], sorted(c["services"])
assert c["services"]["api"]["environment"].get("DATABASE_URL") is None
assert c["services"]["db"]["environment"].get("POSTGRES_PASSWORD") is None
ports = [p for s in c["services"].values() for p in s.get("ports", [])]
assert len(ports) == 3
assert all(p["host_ip"] == "127.0.0.1" for p in ports)
' <<<"$config"

# Compose bind-backed secrets keep source permissions. Prove a uid 1000 process
# can read the reviewed 0444 source mode rather than trusting the Compose model.
mkdir "$tmp/private"
chmod 0750 "$tmp/private"
printf 'test-only-secret\n' >"$tmp/private/value"
chmod 0444 "$tmp/private/value"
# Under Git Bash, /tmp is not a path Docker Desktop knows: give it the Windows one.
secret_file="$tmp/private/value"
if command -v cygpath >/dev/null 2>&1; then secret_file=$(cygpath -m "$secret_file"); fi
cat >"$tmp/secret-compose.yml" <<YAML
services:
  reader:
    image: python:3.12-slim
    user: "1000:1000"
    command: ["python", "-c", "from pathlib import Path; assert Path('/run/secrets/value').read_bytes()"]
    secrets:
      - value
secrets:
  value:
    file: $secret_file
YAML
docker compose -p "$secret_project" -f "$tmp/secret-compose.yml" \
  run --rm reader >/dev/null

echo "deploy-preproduction guards: PASS"
