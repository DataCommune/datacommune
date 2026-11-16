#!/usr/bin/env bash
# Local proofs of the production kit (plan 04.2-01), without any server, ssh or Cloudflare.
#
#   bash infra/prod/tests/test-prod-scripts.sh      # Git Bash on the workstation, or Linux bash
#
# 1. `bash -n` on every script of infra/prod/.
# 2. provision.sh --dry-run with OS_RELEASE_FILE on a temporary Debian 12 file: exit 0, every
#    hardening action printed, no system command run (doubles of apt-get, adduser, ufw,
#    systemctl, ... come first in PATH and log any call); an Ubuntu 24.04 file is refused (80);
#    no --ssh-key is a usage error (2). The public key is generated for the test and deleted.
#    Lockout guards (CR-01, WR-01 of 04.2-REVIEW-CIBLEE.md): --client-ip outside --admin-ip or
#    of the other IP family, no sudo admin account, or an admin account without the key: 80.
# 3. deploy.sh --dry-run <HEAD>: exit 0, prints `git archive`, the release directory and the
#    server block; ssh and scp doubles prove that nothing is contacted.
# 4. deploy.sh --check-env-only: refused on a missing file and on a mode 644 file; with the
#    mode reported as 600 (stat double, Git Bash cannot set it): refused without
#    CF_TUNNEL_TOKEN, refused on a non dc_anon_ key and on the bootstrap placeholder, accepted
#    as first deployment without PUBLIC_DC_API_KEY; --remediate sets a drifted mode back to
#    600 without changing a byte. No value of the file is ever printed.
# 4c. the server block of deploy.sh, run locally against doubles of docker, curl, flock and df
#    (WR-05): first deployment, environment refused before docker, rollback guard, deploy
#    refused during an import (WR-02) or while another one runs (WR-03), pre-deploy dump
#    verified, pruned only after success, refused without room (WR-04).
# 5. `docker compose ... -f api/docker-compose.public.yml -f api/docker-compose.prod.yml
#    config --format json` with a temporary 0600 env file of test values (never used for a
#    deployment, deleted at the end): assertions in Python on the JSON.
# 6. `caddy validate` and `caddy adapt` of api/Caddyfile.prod in `docker run --rm` (caddy
#    image pinned by the stack): five hosts, upstreams, S4 headers, checked on the adapted JSON.
# 7. prod.env.example and README.md name no secret variable with a value.
# Docker missing or its daemon down is a FAILURE, never a skipped step.
# Prints PROD_SCRIPTS_OK only if every check passes; otherwise names the failures, exits 1.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
PROD="$REPO/infra/prod"
# The first interpreter that really runs (python3 can be a Windows Store stub).
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAILURES=()
fail() { FAILURES+=("$1"); echo "FAIL: $1" >&2; }
pass() { echo "ok: $1"; }
# hostpath <path>: the form docker.exe and python.exe accept under Git Bash.
hostpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
# contains <label> <file> <fixed string>...: every string must occur in the file.
contains() {
  local label="$1" file="$2" s; shift 2
  for s in "$@"; do grep -qF -- "$s" "$file" || { fail "$label: missing '$s'"; return 1; }; done
  return 0
}

[ -n "$PY" ] || fail "python not found (needed for the JSON assertions)"

# --- 1. syntax ------------------------------------------------------------------------------
for f in lib.sh provision.sh deploy.sh down.sh psql.sh env-bootstrap.sh tests/test-prod-scripts.sh; do
  if bash -n "$PROD/$f"; then pass "bash -n $f"; else fail "bash -n $f"; fi
done

# --- doubles: any system command reached by a dry run is logged ------------------------------
FAKE_BIN="$WORK/bin"; CALLS="$WORK/calls.log"; mkdir -p "$FAKE_BIN"; : > "$CALLS"
for c in apt-get adduser useradd usermod ufw systemctl sshd tune2fs findmnt fallocate mkswap \
  swapon install chown chmod curl gpg dpkg docker ssh scp; do
  printf '#!/usr/bin/env bash\necho "%s $*" >> "%s"\nexit 0\n' "$c" "$CALLS" > "$FAKE_BIN/$c"
  chmod +x "$FAKE_BIN/$c"
done
# getent is a read-only lookup that provision.sh also makes in --dry-run (the sudo admin
# account, WR-01): its double answers two test accounts and logs nothing.
ADMIN_HOME="$WORK/home/ovhadmin"; KEYLESS_HOME="$WORK/home/keyless"; COMMENTED_HOME="$WORK/home/commented"
mkdir -p "$ADMIN_HOME/.ssh" "$KEYLESS_HOME/.ssh" "$COMMENTED_HOME/.ssh"; : > "$KEYLESS_HOME/.ssh/authorized_keys"
cat > "$FAKE_BIN/getent" <<EOF
#!/usr/bin/env bash
case "\$1 \$2" in
  "passwd ovhadmin") echo "ovhadmin:x:1001:1001::$ADMIN_HOME:/bin/bash" ;;
  "passwd keyless") echo "keyless:x:1002:1002::$KEYLESS_HOME:/bin/bash" ;;
  "passwd commented") echo "commented:x:1003:1003::$COMMENTED_HOME:/bin/bash" ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$FAKE_BIN/getent"

# --- 2. provision.sh --------------------------------------------------------------------------
ssh-keygen -q -t ed25519 -N '' -C prod-test -f "$WORK/testkey" >/dev/null 2>&1 ||
  fail "ssh-keygen unavailable (throwaway public key for the dry run)"
KEY="$(cat "$WORK/testkey.pub" 2>/dev/null || true)"
# The admin account holds the same key, behind an options prefix and another comment; the
# "commented" account only in a commented-out line.
printf 'no-agent-forwarding %s ovh-console\n' "$(printf '%s' "$KEY" | cut -d' ' -f1,2)" > "$ADMIN_HOME/.ssh/authorized_keys"
printf '# %s\n' "$KEY" > "$COMMENTED_HOME/.ssh/authorized_keys"
printf 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nNAME="Debian GNU/Linux"\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\nID=debian\n' > "$WORK/os-debian12"
printf 'PRETTY_NAME="Ubuntu 24.04 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\nID=ubuntu\nID_LIKE=debian\n' > "$WORK/os-ubuntu"

out="$WORK/provision.out"
PATH="$FAKE_BIN:$PATH" OS_RELEASE_FILE="$WORK/os-debian12" SUDO_USER=ovhadmin \
  bash "$PROD/provision.sh" --dry-run --ssh-key "$KEY" --admin-ip 203.0.113.7 --client-ip 203.0.113.7 > "$out" 2>&1
rc=$?
if [ "$rc" = 0 ]; then pass "provision --dry-run on Debian 12 exits 0"; else fail "provision --dry-run Debian 12 exit $rc"; sed 's/^/    /' "$out" >&2; fi
contains "provision dry-run" "$out" \
  "apt-get install -y -qq ca-certificates curl gnupg openssl ufw fail2ban unattended-upgrades" \
  "adduser --disabled-password" "--uid 1000 datacommune" \
  "PasswordAuthentication no" "PermitRootLogin no" "KbdInteractiveAuthentication no" \
  "ufw default deny incoming" "ufw allow from 203.0.113.7 to any port 22 proto tcp" "ufw --force enable" \
  "systemd-run --unit=dc-ufw-deadman --on-active=10min /usr/sbin/ufw disable" "systemctl stop dc-ufw-deadman.timer" \
  "backend = systemd" 'Unattended-Upgrade::Automatic-Reboot "false";' "label=Debian-Security" \
  "docker-compose-plugin" "usermod -aG docker datacommune" \
  "install -d -m 0700 -o datacommune -g datacommune /opt/datacommune /opt/datacommune/releases /opt/datacommune/backups" \
  "/opt/datacommune/sources" "datacommune-source-archive v1" "sentinelle_absente" \
  "SystemMaxUse=2G" "tune2fs -m 1" "fallocate -l 4G" "DRY_RUN provision" &&
  pass "provision dry-run prints every hardening action"
if grep -Eq 'ufw allow[^#]*(80|443)' "$out"; then fail "provision dry-run opens 80/443"; else pass "no 80/443 rule"; fi
# WR-06: ufw and its 10-minute switch come last, the cancel instruction right after enabling.
n_tune="$(grep -n -m1 'tune2fs -m 1' "$out" | cut -d: -f1)"
n_switch="$(grep -n -m1 'systemd-run --unit=dc-ufw-deadman' "$out" | cut -d: -f1)"
n_enable="$(grep -n -m1 '^+ ufw --force enable' "$out" | cut -d: -f1)"
n_next="$(grep -n -m1 '^NEXT, within 10 minutes' "$out" | cut -d: -f1)"
if [ -n "$n_tune" ] && [ -n "$n_switch" ] && [ -n "$n_enable" ] && [ -n "$n_next" ] &&
   [ "$n_switch" -gt "$n_tune" ] && [ "$n_next" = "$((n_enable + 1))" ]; then
  pass "ufw and its switch armed last, cancel instruction right after"
else fail "ufw switch order: tune2fs ${n_tune:-?}, switch ${n_switch:-?}, enable ${n_enable:-?}, instruction ${n_next:-?}"; fi
if [ -s "$CALLS" ]; then fail "provision --dry-run ran system commands: $(tr '\n' ';' < "$CALLS")"; else pass "provision --dry-run ran no system command"; fi

: > "$CALLS"
PATH="$FAKE_BIN:$PATH" OS_RELEASE_FILE="$WORK/os-ubuntu" SUDO_USER=ovhadmin \
  bash "$PROD/provision.sh" --dry-run --ssh-key "$KEY" --admin-ip 203.0.113.7 --client-ip 203.0.113.7 > "$out" 2>&1
rc=$?
if [ "$rc" = 80 ] && grep -q 'REFUSED: Debian 12 required' "$out"; then pass "Ubuntu 24.04 refused (80)"; else fail "Ubuntu 24.04 not refused (exit $rc)"; fi
PATH="$FAKE_BIN:$PATH" OS_RELEASE_FILE="$WORK/os-debian12" SUDO_USER=ovhadmin \
  bash "$PROD/provision.sh" --dry-run --admin-ip 203.0.113.7 --client-ip 203.0.113.7 > "$out" 2>&1
rc=$?
if [ "$rc" = 2 ] && grep -q -- '--ssh-key is required' "$out"; then pass "missing --ssh-key refused (2)"; else fail "missing --ssh-key not refused (exit $rc)"; fi
PATH="$FAKE_BIN:$PATH" OS_RELEASE_FILE="$WORK/os-debian12" SUDO_USER=ovhadmin \
  bash "$PROD/provision.sh" --dry-run --ssh-key "not a key" --admin-ip 203.0.113.7 --client-ip 203.0.113.7 > "$out" 2>&1
rc=$?
if [ "$rc" = 80 ]; then pass "malformed --ssh-key refused (80)"; else fail "malformed --ssh-key not refused (exit $rc)"; fi

# Lockout guards. prov_case <label> <expected exit> <expected text> <args after --ssh-key>;
# PROV_SUDO_USER (default ovhadmin) stands for the account that ran sudo.
prov_case() {
  local label="$1" want="$2" text="$3" rc
  shift 3
  PATH="$FAKE_BIN:$PATH" OS_RELEASE_FILE="$WORK/os-debian12" SUDO_USER="${PROV_SUDO_USER-ovhadmin}" \
    bash "$PROD/provision.sh" --dry-run --ssh-key "$KEY" "$@" > "$out" 2>&1
  rc=$?
  if [ "$rc" = "$want" ] && grep -qF -- "$text" "$out"; then pass "provision: $label ($rc)"
  else fail "provision: $label: exit $rc, expected $want with '$text'"; sed 's/^/    /' "$out" | tail -5 >&2; fi
}
prov_case "client inside an admin /24" 0 "DRY_RUN provision" --admin-ip 203.0.113.0/24 --client-ip 203.0.113.42
prov_case "client outside --admin-ip" 80 "is not inside --admin-ip" --admin-ip 203.0.113.7 --client-ip 198.51.100.9
prov_case "client outside an admin /30" 80 "is not inside --admin-ip" --admin-ip 203.0.113.4/30 --client-ip 203.0.113.8
prov_case "IPv6 client, IPv4 --admin-ip" 80 "is IPv6, --admin-ip 203.0.113.7 is IPv4" --admin-ip 203.0.113.7 --client-ip 2001:db8::7
prov_case "missing --client-ip" 2 "--client-ip is required" --admin-ip 203.0.113.7
PROV_SUDO_USER="" prov_case "run as root, no sudo admin account" 80 "non-root admin account" --admin-ip 203.0.113.7 --client-ip 203.0.113.7
PROV_SUDO_USER=root prov_case "sudo from root" 80 "non-root admin account" --admin-ip 203.0.113.7 --client-ip 203.0.113.7
PROV_SUDO_USER=keyless prov_case "admin account without the key" 80 "keeps no SSH access with this key" --admin-ip 203.0.113.7 --client-ip 203.0.113.7
PROV_SUDO_USER=commented prov_case "admin key only in a commented line" 80 "keeps no SSH access with this key" --admin-ip 203.0.113.7 --client-ip 203.0.113.7
if [ -s "$CALLS" ]; then fail "refused provision runs ran system commands"; fi

# --- 3. deploy.sh --dry-run -------------------------------------------------------------------
HEAD_SHA="$(git -C "$REPO" rev-parse HEAD)"
out="$WORK/deploy.out"; : > "$CALLS"
# The commit is on the public master here (PROD_PUBLISHED_REF names it); 3b refuses otherwise.
PATH="$FAKE_BIN:$PATH" PROD_SETTINGS_FILE="$WORK/none.env" DEPLOYMENT_HOST=prod-test.invalid \
  PROD_PUBLISHED_REF="$HEAD_SHA" bash "$PROD/deploy.sh" --dry-run "$HEAD_SHA" > "$out" 2>&1
rc=$?
if [ "$rc" = 0 ]; then pass "deploy --dry-run exits 0"; else fail "deploy --dry-run exit $rc"; sed 's/^/    /' "$out" >&2; fi
contains "deploy dry-run" "$out" \
  "archive --format=tar.gz" "$HEAD_SHA api frontend" \
  "/opt/datacommune/releases/${HEAD_SHA:0:12}" \
  "+ scp -q -o BatchMode=yes" "datacommune@prod-test.invalid:/tmp/dc-prod-${HEAD_SHA:0:12}.tgz" \
  "+ ssh datacommune@prod-test.invalid bash" \
  "-f api/docker-compose.public.yml -f api/docker-compose.prod.yml" \
  "alembic upgrade head" "scripts.rotate_anon_key" "DEPLOYED" &&
  pass "deploy dry-run prints git archive, release and server block"
if [ -s "$CALLS" ]; then fail "deploy --dry-run contacted something: $(tr '\n' ';' < "$CALLS")"; else pass "deploy --dry-run: no ssh, no scp"; fi
if ls "${TMPDIR:-/tmp}"/tmp.*/release-"${HEAD_SHA:0:12}".tgz >/dev/null 2>&1; then fail "deploy --dry-run left an archive"; fi
# --- 3b. only a commit of the public master is deployed (D-135, 05-07 lot 4) -------------------
pub_case() { # <name> <expected rc> <expected text> <published ref> [deploy args...]
  local name="$1" want="$2" text="$3" ref="$4"; shift 4
  : > "$CALLS"
  PATH="$FAKE_BIN:$PATH" PROD_SETTINGS_FILE="$WORK/none.env" DEPLOYMENT_HOST=prod-test.invalid \
    PROD_PUBLISHED_REF="$ref" bash "$PROD/deploy.sh" "$@" > "$WORK/pub.out" 2>&1
  local got=$?
  if [ "$got" = "$want" ] && grep -q -- "$text" "$WORK/pub.out"; then pass "$name"; else
    fail "$name: exit $got (want $want)"; tail -5 "$WORK/pub.out" >&2; fi
  if [ "$want" = 80 ] && [ -s "$CALLS" ]; then fail "$name: contacted something"; fi
}
pub_case "unpublished commit refused" 80 "is not on" "$HEAD_SHA~1" --dry-run "$HEAD_SHA"
pub_case "unknown public ref refused" 80 "is unknown" refs/remotes/nowhere/master --dry-run "$HEAD_SHA"
pub_case "--allow-unpublished deploys with a warning" 0 "WARNING: $HEAD_SHA is not on" "$HEAD_SHA~1" \
  --dry-run --allow-unpublished "$HEAD_SHA"

# The server blocks are quoted heredocs, invisible to `bash -n` of their script: check the
# text the dry runs print, which is exactly what the server would evaluate.
blk="$WORK/block.sh"
sed -n 's/^    | //p' "$out" > "$blk"
if [ -s "$blk" ] && bash -n "$blk"; then pass "deploy server block: bash -n"; else fail "deploy server block: bash -n"; fi
for s in "down.sh" "psql.sh SELECT-1"; do
  set -- $s
  PROD_SETTINGS_FILE="$WORK/none.env" DEPLOYMENT_HOST=prod-test.invalid \
    bash "$PROD/$1" --dry-run ${2:+"SELECT 1"} 2>/dev/null | sed -n 's/^    | //p' > "$blk"
  if [ -s "$blk" ] && bash -n "$blk"; then pass "$1 server block: bash -n"; else fail "$1 server block: bash -n"; fi
done
set --

# --- 4. deploy.sh --check-env-only -------------------------------------------------------------
STAT_BIN="$WORK/statbin"; mkdir -p "$STAT_BIN"
REAL_STAT="$(command -v stat)"; REAL_CHMOD="$(command -v chmod)"
# Mode doubles: a <file>.mode sidecar stands for the mode bits Git Bash cannot set, a
# <path>.uid sidecar for the owner UID (server block, section 4c).
cat > "$STAT_BIN/stat" <<EOF
#!/usr/bin/env bash
if [ "\$1" = -c ] && [ "\$2" = %a ] && [ -f "\$3.mode" ]; then cat "\$3.mode"; exit 0; fi
if [ "\$1" = -c ] && [ "\$2" = %u ] && [ -f "\$3.uid" ]; then cat "\$3.uid"; exit 0; fi
exec "$REAL_STAT" "\$@"
EOF
cat > "$STAT_BIN/chmod" <<EOF
#!/usr/bin/env bash
if [ "\$#" = 2 ] && [ -f "\$2.mode" ]; then echo "\$1" > "\$2.mode"; exit 0; fi
exec "$REAL_CHMOD" "\$@"
EOF
"$REAL_CHMOD" +x "$STAT_BIN/stat" "$STAT_BIN/chmod"

# Test values only: recognisable, not secrets, never used for a deployment.
TV=test-value-not-a-secret
base_env() {
  printf 'POSTGRES_DB=datacommune\nPOSTGRES_USER=datacommune\n'
  printf 'POSTGRES_PASSWORD=%s\nJWT_SECRET_KEY=%s\nAPI_KEY_HMAC_SECRET=%s\nADMIN_PASSWORD=%s\n' "$TV" "$TV" "$TV" "$TV"
}
# check <label> <expected exit> <expected text> <file> [--remediate]
check() {
  local label="$1" want="$2" text="$3" file="$4" extra="${5:-}" o="$WORK/check.out" rc
  # shellcheck disable=SC2086
  PATH="$STAT_BIN:$PATH" bash "$PROD/deploy.sh" --check-env-only "$file" $extra > "$o" 2>&1
  rc=$?
  if [ "$rc" = "$want" ] && grep -qF -- "$text" "$o"; then pass "check-env-only: $label ($rc)"
  else fail "check-env-only: $label: exit $rc, expected $want with '$text'"; sed 's/^/    /' "$o" >&2; fi
  if grep -qF -e "$TV" -e "tunnel-test-value" "$o"; then fail "check-env-only: $label printed a value"; fi
}
E="$WORK/env"; mkdir -p "$E"
check "missing file" 80 "missing" "$E/absent.env"
{ base_env; echo "CF_TUNNEL_TOKEN=tunnel-test-value"; } > "$E/m644.env"; "$REAL_CHMOD" 644 "$E/m644.env"
check "mode 644" 80 "mode 644, must be 600" "$E/m644.env"
{ base_env; } > "$E/notoken.env"; echo 600 > "$E/notoken.env.mode"
check "no CF_TUNNEL_TOKEN" 80 "CF_TUNNEL_TOKEN defined 0 time(s)" "$E/notoken.env"
{ base_env; echo "CF_TUNNEL_TOKEN=tunnel-test-value"; } > "$E/first.env"; echo 600 > "$E/first.env.mode"
check "first deployment (no PUBLIC_DC_API_KEY)" 0 "ENV_OK first deployment" "$E/first.env"
{ base_env; echo "CF_TUNNEL_TOKEN=tunnel-test-value"; echo "PUBLIC_DC_API_KEY=test-value-not-a-key"; } > "$E/badkey.env"; echo 600 > "$E/badkey.env.mode"
check "key without dc_anon_" 80 "not a dc_anon_* key" "$E/badkey.env"
{ base_env; echo "CF_TUNNEL_TOKEN=tunnel-test-value"; echo "PUBLIC_DC_API_KEY=dc_anon_bootstrap"; } > "$E/placeholder.env"; echo 600 > "$E/placeholder.env.mode"
check "bootstrap placeholder" 80 "bootstrap placeholder" "$E/placeholder.env"
{ base_env; echo "CF_TUNNEL_TOKEN=tunnel-test-value"; } > "$E/drift.env"; echo 640 > "$E/drift.env.mode"
before="$(sha256sum < "$E/drift.env")"
check "mode 640 without --remediate" 80 "mode 640, must be 600" "$E/drift.env"
check "mode 640 with --remediate" 0 "REMEDIATED" "$E/drift.env" --remediate
if [ "$(cat "$E/drift.env.mode")" = 600 ] && [ "$(sha256sum < "$E/drift.env")" = "$before" ]; then
  pass "remediation: mode 600, content unchanged"
else fail "remediation changed the content or not the mode"; fi

# --- 4b. env-bootstrap.sh: generated on the target, names only, idempotent ----------------------
B="$WORK/boot"; mkdir -p "$B"; BOUT="$WORK/boot.out"
printf 'tunnel-test-value\n' | ENV_FILE="$B/.env" ENV_OWNER="" bash "$PROD/env-bootstrap.sh" --token-stdin > "$BOUT" 2>&1
rc=$?
if [ "$rc" = 0 ] && grep -q '^ENV_BOOTSTRAP_OK' "$BOUT"; then pass "env-bootstrap first run"; else fail "env-bootstrap first run exit $rc"; sed 's/^/    /' "$BOUT" >&2; fi
for n in POSTGRES_PASSWORD JWT_SECRET_KEY API_KEY_HMAC_SECRET ADMIN_PASSWORD CF_TUNNEL_TOKEN POSTGRES_DB \
  POSTGRES_USER DEBUG APP_ENVIRONMENT EMAIL_ENABLED STRIPE_ENABLED; do
  [ "$(grep -c "^$n=" "$B/.env" 2>/dev/null)" = 1 ] || fail "env-bootstrap: $n not present exactly once"
done
grep -Eq '^POSTGRES_PASSWORD=[0-9a-f]{64}$' "$B/.env" && grep -Eq '^ADMIN_PASSWORD=[0-9a-f]{48}$' "$B/.env" ||
  fail "env-bootstrap: secrets not generated by openssl rand"
if grep -q '^PUBLIC_DC_API_KEY=' "$B/.env"; then fail "env-bootstrap wrote PUBLIC_DC_API_KEY"; fi
if grep -Eq '[0-9a-f]{32}|tunnel-test-value' "$BOUT"; then fail "env-bootstrap printed a value"; else pass "env-bootstrap prints names only"; fi
before="$(sha256sum < "$B/.env")"
printf 'another-test-value\n' | ENV_FILE="$B/.env" ENV_OWNER="" bash "$PROD/env-bootstrap.sh" --token-stdin > "$BOUT" 2>&1
if [ "$(sha256sum < "$B/.env")" = "$before" ] && grep -q 'kept: CF_TUNNEL_TOKEN' "$BOUT"; then
  pass "env-bootstrap rerun keeps every value, token never replaced"
else fail "env-bootstrap rerun changed the file"; fi
echo 600 > "$B/.env.mode"
check "file written by env-bootstrap" 0 "ENV_OK first deployment" "$B/.env"
{ base_env; echo "POSTGRES_PASSWORD=$TV"; echo "CF_TUNNEL_TOKEN=tunnel-test-value"; } > "$E/twice.env"; echo 600 > "$E/twice.env.mode"
check "secret defined twice" 80 "POSTGRES_PASSWORD defined 2 time(s)" "$E/twice.env"
{ base_env; echo "CF_TUNNEL_TOKEN="; } > "$E/empty.env"; echo 600 > "$E/empty.env.mode"
check "empty secret" 80 "CF_TUNNEL_TOKEN empty" "$E/empty.env"
{ base_env; echo "CF_TUNNEL_TOKEN=tunnel-test-value"; echo "PUBLIC_DC_API_KEY=dc_anon_x"; echo "PUBLIC_DC_API_KEY=dc_anon_y"; } > "$E/twokeys.env"
echo 600 > "$E/twokeys.env.mode"
check "PUBLIC_DC_API_KEY twice" 80 "PUBLIC_DC_API_KEY defined 2 times" "$E/twokeys.env"

# --- 4c. server block of deploy.sh, run locally (WR-05) ------------------------------------------
# The dry run of section 3 printed exactly what the server evaluates: an `export` prelude, then
# ENV_CHECK_FN and the server block. The prelude is replaced by test values. docker, curl, flock
# and df are doubles: docker plays a scripted server from the files of a state directory and
# logs every call with the PUBLIC_DC_API_KEY / CF_TUNNEL_TOKEN values in its environment.
SRV_BLOCK="$WORK/server-block.sh"
{ echo 'set -euo pipefail'; sed -n 's/^    | //p' "$WORK/deploy.out" | awk 'body || !/^export /{body = 1; print}'; } > "$SRV_BLOCK"
SRV_BIN="$WORK/srvbin"; mkdir -p "$SRV_BIN"
cat > "$SRV_BIN/docker" <<'EOF'
#!/usr/bin/env bash
S="$FAKE_STATE"
printf 'docker %s | KEY=%s TOKEN=%s\n' "$*" "${PUBLIC_DC_API_KEY-unset}" "${CF_TUNNEL_TOKEN-unset}" >> "$S/calls"
case "$1" in
  compose)
    shift
    while [ "$#" -gt 0 ]; do case "$1" in -p|--env-file|-f) shift 2 ;; *) break ;; esac; done
    sub="$1"; shift
    case "$sub" in
      up) if [ "$*" = "-d --no-build --no-deps db" ]; then touch "$S/db"; else touch "$S/db" "$S/api" "$S/frontend" "$S/frontend-prod"; fi ;;
      run)
        case "$*" in
          *"--entrypoint id api -u"*) echo 1000 ;;
          *"alembic upgrade head"*) [ ! -e "$S/alembic_fails" ] || exit 1; echo t > "$S/has_alembic" ;;
          *"scripts.rotate_anon_key"*)
            # Same output as api/backend/scripts/rotate_anon_key.py (log line on stderr).
            [ "$(cat)" = ROTATE ] || exit 1
            line="============================================================"
            printf "\nWARNING: This script inserts a new dc_anon_* API key into the production DB.\nIt does NOT revoke the old dc_live_* key (see step 4 above).\n\nType 'ROTATE' to proceed: \n%s\nNEW dc_anon_* KEY GENERATED:\n%s\n%s\n\nNEXT STEPS (two-step rollout - do NOT skip):\n" "$line" "$(cat "$S/cli_key")" "$line"
            echo "2026-10-24 12:00:00,000 INFO Inserted dc_anon_* key with id=7, prefix=dc_anon_testtest..." >&2 ;;
        esac ;;
    esac ;;
  ps)
    svc="" oneoff=""
    for a in "$@"; do
      case "$a" in
        label=com.docker.compose.service=*) svc="${a#label=com.docker.compose.service=}" ;;
        label=com.docker.compose.oneoff=*) oneoff="${a#label=com.docker.compose.oneoff=}" ;;
      esac
    done
    # Newest first, like docker ps: a `compose run` api container started after the service.
    if [ "$svc" = api ] && [ "$oneoff" != False ] && [ -e "$S/oneoff_api" ]; then echo cid-oneoff-api; fi
    if [ "$oneoff" != True ] && [ -e "$S/${svc:-none}" ]; then echo "cid-$svc"; fi ;;
  inspect) echo healthy ;;
  stop) rm -f "$S/${2#cid-}"; [ "$2" != cid-oneoff-api ] || rm -f "$S/oneoff_api" ;;
  start) touch "$S/${2#cid-}" ;;
  exec)
    case "$*" in
      *pg_dump*) printf 'PGDMP test dump\n' ;;
      *pg_restore*) cat > /dev/null; [ ! -e "$S/dump_unreadable" ] || exit 1 ;;
      *psql*)
        sql="$(cat)"
        case "$sql" in
          *pg_database_size*) cat "$S/db_size" ;;
          *to_regclass*) cat "$S/has_alembic" ;;
          *version_num*) cat "$S/revision" ;;
          *"count(*)"*) cat "$S/user_tables" ;;
        esac ;;
    esac ;;
esac
exit 0
EOF
cat > "$SRV_BIN/curl" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  -sI*) printf 'HTTP/1.1 200 OK\nstrict-transport-security: max-age=86400\nx-robots-tag: noindex, nofollow\n' ;;
  *__version*) printf '{"deployment_sha":"%s"}\n' "$SHA" ;;
esac
EOF
cat > "$SRV_BIN/flock" <<'EOF'
#!/usr/bin/env bash
printf 'flock %s\n' "$*" >> "$FAKE_STATE/calls"
[ ! -e "$FAKE_STATE/lock_held" ]
EOF
# Git Bash cannot set Unix modes on its temporary directories: `install -d -m` only creates them.
cat > "$SRV_BIN/install" <<'EOF'
#!/usr/bin/env bash
[ "$1" = -d ] || exit 1
shift; [ "$1" != -m ] || shift 2
mkdir -p "$@"
EOF
cat > "$SRV_BIN/df" <<'EOF'
#!/usr/bin/env bash
printf 'Filesystem 1-blocks Used Available Capacity Mounted on\n/dev/test 0 0 %s 0%% /\n' "$(cat "$FAKE_STATE/df_avail")"
EOF
"$REAL_CHMOD" +x "$SRV_BIN"/*

SRV_SHA="$HEAD_SHA"; SRV_TAG="${HEAD_SHA:0:12}"
SRV_KEY="dc_anon_$(printf 'served%.0s' 1 2 3 4)"; CLI_KEY="dc_anon_$(printf 'cli%.0s' 1 2 3 4 5 6 7 8)"
# new_srv <name>: a served base at the head of the release, the api running, a real key in the
# private file, room for a dump; each scenario changes only what it tests. Prints the directory.
new_srv() {
  local d="$WORK/srv/$1" rel
  rel="$d/opt/releases/$SRV_TAG"
  mkdir -p "$d/state" "$rel/api/backend/migrations/versions" "$d/opt/sources" "$d/opt/backups"
  touch "$rel/.complete" "$d/state/calls" "$d/state/db" "$d/state/api"
  printf 'revision = "rev_a"\ndown_revision = None\n' > "$rel/api/backend/migrations/versions/0001_a.py"
  printf 'revision: str = "rev_b"\ndown_revision: str | None = "rev_a"\n' > "$rel/api/backend/migrations/versions/0002_b.py"
  echo "datacommune-source-archive v1" > "$d/opt/sources/.datacommune-source-archive"; echo 1000 > "$d/opt/sources.uid"
  { base_env; echo "CF_TUNNEL_TOKEN=tunnel-test-value"; echo "PUBLIC_DC_API_KEY=$SRV_KEY"; } > "$d/opt/.env"; echo 600 > "$d/opt/.env.mode"
  echo t > "$d/state/has_alembic"; echo rev_b > "$d/state/revision"; echo 3 > "$d/state/user_tables"
  echo 1000000 > "$d/state/db_size"; echo 1000000000000 > "$d/state/df_avail"; echo "$CLI_KEY" > "$d/state/cli_key"
  printf '%040d\n' 1 > "$d/opt/current_sha"
  echo "$d"
}
# srv <dir> <mode>: runs the server block; exit code in SRV_RC, output in <dir>/out.
srv() {
  ( export MODE="$2" SHA="$SRV_SHA" TAG="$SRV_TAG" DIR="$1/opt" ENV_FILE="$1/opt/.env" PROJECT=datacommune-prod \
      NO_TUNNEL=0 ANON_KEY_QUOTA=500 PROD_DOMAIN=prod.datacommune.fr PROD_API_DOMAIN=api-prod.datacommune.fr \
      APEX_DOMAIN=datacommune.fr API_DOMAIN=api.datacommune.fr FAKE_STATE="$1/state" PATH="$SRV_BIN:$STAT_BIN:$PATH"
    bash "$SRV_BLOCK" ) > "$1/out" 2>&1
  SRV_RC=$?
}
# srv_expect <label> <dir> <exit> <text>
srv_expect() {
  if [ "$SRV_RC" = "$3" ] && grep -qF -- "$4" "$2/out"; then pass "server block: $1 ($SRV_RC)"
  else fail "server block: $1: exit $SRV_RC, expected $3 with '$4'"; sed 's/^/    /' "$2/out" | tail -8 >&2; fi
}
called() { grep -qE -- "$2" "$1/state/calls"; }   # called <dir> <ERE>

if [ -s "$WORK/deploy.out" ] && bash -n "$SRV_BLOCK"; then
  d="$(new_srv deploy)"; srv "$d" deploy
  srv_expect "deploy over a served release" "$d" 0 "DEPLOYED $SRV_SHA"
  if called "$d" '^docker stop cid-api '; then pass "server block: api service stopped before the migration"
  else fail "server block: api service not stopped before the migration"; fi
  [ "$(cat "$d/opt/current_sha")" = "$SRV_SHA" ] && [ "$(cat "$d/opt/previous_sha")" = "$(printf '%040d' 1)" ] ||
    fail "server block: current_sha / previous_sha not recorded"

  # First deployment: the placeholders reach only the api build, the db start, the one-off api
  # runs (image checks, migration, key); the CLI key lands once in the private file, never printed.
  d="$(new_srv first)"
  grep -v '^PUBLIC_DC_API_KEY=' "$d/opt/.env" > "$d/opt/.env.new"; mv "$d/opt/.env.new" "$d/opt/.env"
  rm -f "$d/state/api" "$d/opt/current_sha"; echo f > "$d/state/has_alembic"; echo 0 > "$d/state/user_tables"
  srv "$d" deploy
  srv_expect "first deployment" "$d" 0 "DEPLOYED $SRV_SHA"
  if [ "$(grep -c '^PUBLIC_DC_API_KEY=' "$d/opt/.env")" = 1 ] && grep -qxF "PUBLIC_DC_API_KEY=$CLI_KEY" "$d/opt/.env"; then
    pass "first deployment: the CLI key written once to the private file"
  else fail "first deployment: PUBLIC_DC_API_KEY is not the single CLI key line"; fi
  if ls "$d/opt"/.anon-key.* >/dev/null 2>&1; then fail "first deployment: key output file left on disk"; else pass "first deployment: key output file destroyed"; fi
  if grep -qF -- "$CLI_KEY" "$d/out"; then fail "first deployment printed the key"; else pass "first deployment: key never printed"; fi
  leak="$(grep -E 'KEY=dc_anon_bootstrap|TOKEN=bootstrap' "$d/state/calls" |
    grep -vE ' (build api|up -d --no-build --no-deps db|run --rm -T --no-deps .*) \| KEY=' || true)"
  if [ -z "$leak" ] && called "$d" ' build frontend frontend-prod \| KEY=unset TOKEN=unset$'; then
    pass "first deployment: frontends and tunnel never see the placeholders"
  else fail "first deployment: placeholders beyond api build/db/one-off runs: ${leak:-frontends not built}"; fi

  d="$(new_srv dup-secret)"; echo "JWT_SECRET_KEY=$TV" >> "$d/opt/.env"; srv "$d" deploy
  srv_expect "secret defined twice refused on the server" "$d" 80 "JWT_SECRET_KEY defined 2 time(s)"
  if called "$d" '^docker '; then fail "server block: a refused environment still reached docker"; else pass "server block: refused environment, docker never called"; fi

  d="$(new_srv rollback-head)"; echo "$SRV_SHA" > "$d/opt/previous_sha"; srv "$d" rollback
  srv_expect "rollback onto a base at the head of the release" "$d" 0 "DEPLOYED $SRV_SHA"
  d="$(new_srv rollback-behind)"; echo "$SRV_SHA" > "$d/opt/previous_sha"; echo rev_a > "$d/state/revision"; srv "$d" rollback
  srv_expect "rollback refused on a revision that is not the head" "$d" 80 "not the head of release"
  if called "$d" ' up -d'; then fail "server block: refused rollback started something"; fi

  # WR-02: a `compose run` api container (national import) is not the api service.
  d="$(new_srv import-running)"; touch "$d/state/oneoff_api"; srv "$d" deploy
  srv_expect "deploy refused while a one-off api container runs" "$d" 80 "one-off api container"
  if called "$d" '^docker stop '; then fail "server block: deploy stopped a container during an import"; else pass "server block: nothing stopped during an import"; fi

  # WR-03: one deploy or rollback at a time.
  d="$(new_srv locked)"; touch "$d/state/lock_held"; srv "$d" deploy
  srv_expect "second deploy refused while one runs" "$d" 80 "another deploy or rollback is running"
  if called "$d" '^docker '; then fail "server block: locked deploy still reached docker"; fi

  # WR-04: dump verified before the migration, kept until a release is healthy, room checked.
  d="$(new_srv dump-unreadable)"; touch "$d/state/dump_unreadable"; srv "$d" deploy
  srv_expect "unreadable dump stops the deploy" "$d" 81 "unreadable by pg_restore"
  if ls "$d/opt/backups" | grep -q .; then fail "server block: an unverified dump is kept in backups"; else pass "server block: unverified dump removed"; fi
  if called "$d" 'alembic upgrade head'; then fail "server block: migrated after an unverified dump"; fi
  d="$(new_srv prune)"
  for i in 1 2 3 4; do echo old > "$d/opt/backups/pre-old$i-2026010${i}T000000.pgdump"; touch -d "2026-01-0$i" "$d/opt/backups/pre-old$i-2026010${i}T000000.pgdump"; done
  touch "$d/state/alembic_fails"; srv "$d" deploy
  srv_expect "failed migration" "$d" 81 "alembic upgrade head"
  if [ "$(ls "$d/opt/backups" | grep -c '^pre-old')" = 4 ]; then pass "server block: a failed deploy prunes no dump"; else fail "server block: a failed deploy pruned dumps"; fi
  rm -f "$d/state/alembic_fails"; srv "$d" deploy
  srv_expect "deploy after the failed one" "$d" 0 "DEPLOYED $SRV_SHA"
  if [ "$(ls "$d/opt/backups" | grep -c '\.pgdump$')" = 3 ] && ! ls "$d/opt/backups" | grep -q '^pre-old[12]-'; then
    pass "server block: healthy deploy keeps the 3 newest dumps"
  else fail "server block: dumps after a healthy deploy: $(ls "$d/opt/backups" | tr '\n' ' ')"; fi
  d="$(new_srv no-room)"; echo 999999 > "$d/state/df_avail"; srv "$d" deploy
  srv_expect "no room for the dump" "$d" 80 "no room for the pre-deploy dump"
  if called "$d" 'pg_dump|^docker stop '; then fail "server block: dumped or stopped without room"; fi
else
  fail "server block not extracted from the dry run (section 3)"
fi

# --- 5. compose config (JSON) -------------------------------------------------------------------
docker_ok=1
if ! command -v docker >/dev/null 2>&1; then fail "docker CLI not found (compose and Caddy checks cannot run)"; docker_ok=0
elif ! docker info >/dev/null 2>&1; then fail "docker daemon unavailable (compose and Caddy checks cannot run)"; docker_ok=0
fi
if [ "$docker_ok" = 1 ]; then
  ENVF="$(umask 077; mktemp "$WORK/compose-env.XXXXXX")"
  { base_env; echo "CF_TUNNEL_TOKEN=tunnel-test-value"; echo "PUBLIC_DC_API_KEY=test-value-not-a-key"; } > "$ENVF"
  CFG="$WORK/config.json"
  if ( cd "$REPO" && IMAGE_TAG=testtag DEPLOYMENT_SHA=0123456789abcdef docker compose -p datacommune-prod \
      --env-file "$(hostpath "$ENVF")" -f api/docker-compose.public.yml -f api/docker-compose.prod.yml \
      config --format json > "$CFG" 2> "$WORK/config.err" ); then
    pass "docker compose config (public + prod overlay)"
    if "$PY" - "$(hostpath "$CFG")" <<'PY'
import json, sys
c = json.load(open(sys.argv[1], encoding="utf-8"))
s = c["services"]
errors = []
def check(cond, msg):
    if not cond:
        errors.append(msg)
check(c.get("name") == "datacommune-prod", "project name")
# goatcounter (audience count, 04.7-14b) comes from the public file; the overlay keeps it.
check(set(s) == {"db", "api", "frontend", "frontend-prod", "caddy", "cloudflared", "goatcounter"}, f"services {sorted(s)}")
def norm(p):
    return str(p or "").replace("\\", "/")
api = s["api"]; env = api.get("environment", {})
check(env.get("SOURCE_ARCHIVE_DIR") == "/sources", "api SOURCE_ARCHIVE_DIR")
check(env.get("SOURCE_ARCHIVE_MIN_FREE_BYTES") == "375809638400", "api SOURCE_ARCHIVE_MIN_FREE_BYTES")
check(json.loads(env.get("CORS_ORIGINS", "[]")) == ["https://datacommune.fr", "https://prod.datacommune.fr"], "api CORS_ORIGINS")
check(env.get("API_BASE_URL") == "https://api.datacommune.fr", "api API_BASE_URL")
src = [v for v in api.get("volumes", []) if v.get("target") == "/sources"]
check(len(src) == 1 and src[0].get("type") == "bind" and norm(src[0].get("source")).endswith("/opt/datacommune/sources")
      and not src[0].get("read_only", False), f"api /sources bind rw: {src}")
check(api.get("image") == "datacommune-prod-api:testtag", "api image")
fe, fp = s["frontend"], s["frontend-prod"]
check(fe.get("image") == "datacommune-prod-frontend-preprod:testtag", "frontend image")
check(fe["build"]["args"].get("VITE_API_URL") == "https://api-prod.datacommune.fr", "frontend VITE_API_URL")
check(fe["environment"].get("ORIGIN") == "https://prod.datacommune.fr", "frontend ORIGIN")
check(fp.get("image") == "datacommune-prod-frontend:testtag", "frontend-prod image")
check(fp["build"]["args"].get("VITE_API_URL") == "https://api.datacommune.fr", "frontend-prod VITE_API_URL")
check(fp["environment"].get("ORIGIN") == "https://datacommune.fr", "frontend-prod ORIGIN")
check(norm(fp["build"].get("context")) == norm(fe["build"].get("context")), "same build context")
for k in ("ADDRESS_HEADER", "HOST", "PORT", "API_INTERNAL_URL", "FICHE_QUOTA_WEEKLY"):
    check(k in fe["environment"] and fp["environment"].get(k) == fe["environment"].get(k), f"runtime {k} differs")
check(fp["environment"].get("API_INTERNAL_URL") == "http://api:8000", "API_INTERNAL_URL")
cad = s["caddy"]
cf = [v for v in cad.get("volumes", []) if v.get("target") == "/etc/caddy/Caddyfile"]
check(len(cf) == 1 and norm(cf[0].get("source")).endswith("/api/Caddyfile.prod") and cf[0].get("read_only"), f"caddy Caddyfile.prod: {cf}")
check("frontend-prod" in cad.get("depends_on", {}), "caddy depends on frontend-prod")
pub = {n: v.get("ports") for n, v in s.items() if v.get("ports")}
# Exactly two published ports, both on the loopback: Caddy for the tunnel, the GoatCounter
# dashboard for an SSH tunnel. Nothing else, and nothing outside 127.0.0.1.
ports = {n: [(p.get("host_ip"), str(p.get("published")), p.get("target")) for p in v] for n, v in pub.items()}
check(ports == {"caddy": [("127.0.0.1", "8080", 80)], "goatcounter": [("127.0.0.1", "8081", 8080)]}, f"published ports {pub}")
check(all(p[0] == "127.0.0.1" for v in ports.values() for p in v), f"a port is published outside the loopback: {pub}")
cmd = s["db"].get("command") or []
for opt in ("shared_buffers=8GB", "effective_cache_size=20GB", "work_mem=32MB", "maintenance_work_mem=2GB",
            "max_wal_size=16GB", "min_wal_size=2GB", "checkpoint_timeout=15min", "random_page_cost=1.1",
            "max_parallel_maintenance_workers=4", "shared_preload_libraries=pg_stat_statements"):
    check(opt in cmd, f"db {opt}")
for off in ("fsync=off", "synchronous_commit=off", "full_page_writes=off"):
    check(off not in cmd, f"db durability {off}")
check(str(s["db"].get("shm_size")).lower() in ("2gb", "2147483648"), f"db shm_size {s['db'].get('shm_size')}")
if errors:
    print("COMPOSE_JSON_BAD: " + "; ".join(errors))
    sys.exit(1)
print("COMPOSE_JSON_OK")
PY
    then pass "compose JSON assertions"; else fail "compose JSON assertions"; fi
  else
    fail "docker compose config failed"; sed 's/^/    /' "$WORK/config.err" >&2
  fi
  rm -f "$ENVF"

  # --- 6. Caddy ----------------------------------------------------------------------------------
  CADDY_IMAGE="$(sed -n 's/^[[:space:]]*image:[[:space:]]*\(caddy:[^[:space:]]*\).*/\1/p' "$REPO/api/docker-compose.public.yml" | head -1)"
  if [ -z "$CADDY_IMAGE" ]; then fail "caddy image not found in api/docker-compose.public.yml"
  else
    MNT="$(hostpath "$REPO/api/Caddyfile.prod"):/etc/caddy/Caddyfile:ro"
    if MSYS_NO_PATHCONV=1 docker run --rm -v "$MNT" "$CADDY_IMAGE" \
        caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile > "$WORK/caddy-validate.out" 2>&1 &&
       grep -q 'Valid configuration' "$WORK/caddy-validate.out"; then
      pass "caddy validate api/Caddyfile.prod"
    else fail "caddy validate"; tail -5 "$WORK/caddy-validate.out" | sed 's/^/    /' >&2; fi
    if MSYS_NO_PATHCONV=1 docker run --rm -v "$MNT" "$CADDY_IMAGE" \
        caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile > "$WORK/caddy.json" 2> "$WORK/caddy-adapt.err"; then
      if "$PY" - "$(hostpath "$WORK/caddy.json")" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
routes = [r for srv in cfg["apps"]["http"]["servers"].values() for r in srv.get("routes", [])]
by_host = {}
for r in routes:
    for m in r.get("match", []):
        for h in m.get("host", []):
            by_host[h] = json.dumps(r.get("handle", []))
errors = []
want = {"prod.datacommune.fr", "api-prod.datacommune.fr", "datacommune.fr", "api.datacommune.fr", "www.datacommune.fr"}
if set(by_host) != want:
    errors.append(f"hosts {sorted(by_host)}")
s4 = ("X-Content-Type-Options", "nosniff", "X-Frame-Options", "DENY", "Referrer-Policy", "Permissions-Policy")
def need(host, *subs):
    h = by_host.get(host, "")
    for x in subs:
        if x not in h:
            errors.append(f"{host}: {x}")
for host, up in (("prod.datacommune.fr", "frontend:3000"), ("datacommune.fr", "frontend-prod:3000")):
    # 05-07 lot 5: HSTS of one year; the page CSP is written by each frontend (kit.csp), never here.
    need(host, *s4, up, "Strict-Transport-Security", "max-age=31536000", "X-Forwarded-For", "CF-Connecting-IP")
    if "Content-Security-Policy" in by_host.get(host, ""):
        errors.append(f"{host}: Caddy sets a CSP over the frontend's")
need("prod.datacommune.fr", "X-Robots-Tag")
for host in ("api-prod.datacommune.fr", "api.datacommune.fr"):
    need(host, *s4, "api:8000", "max-age=31536000; includeSubDomains", "X-Robots-Tag", "noindex, nofollow",
         "X-Forwarded-For", "CF-Connecting-IP")
need("www.datacommune.fr", "https://datacommune.fr{http.request.uri}", "301")
if "X-Robots-Tag" in by_host.get("datacommune.fr", ""):
    errors.append("datacommune.fr must stay indexable")
if errors:
    print("CADDY_JSON_BAD: " + "; ".join(errors))
    sys.exit(1)
print("CADDY_JSON_OK")
PY
      then pass "Caddy: five hosts, upstreams and S4 headers"; else fail "Caddy adapted JSON assertions"; fi
    else fail "caddy adapt"; tail -5 "$WORK/caddy-adapt.err" | sed 's/^/    /' >&2; fi
  fi
fi

# --- 7. no secret in the documents -------------------------------------------------------------
SECRET_NAMES='POSTGRES_PASSWORD|JWT_SECRET_KEY|API_KEY_HMAC_SECRET|ADMIN_PASSWORD|CF_TUNNEL_TOKEN|PUBLIC_DC_API_KEY'
if grep -Eq "^[[:space:]]*($SECRET_NAMES)[[:space:]]*=" "$PROD/prod.env.example"; then
  fail "prod.env.example names a secret variable"
else pass "prod.env.example: no secret variable"; fi
if grep -Eq "($SECRET_NAMES)=[A-Za-z0-9]" "$PROD/README.md" || grep -Eq 'dc_(anon|live)_[A-Za-z0-9]{12,}' "$PROD/README.md"; then
  fail "README.md carries a secret-looking value"
else pass "README.md: no secret value"; fi
for f in "$PROD/prod.env" "$REPO/api/.env.prod"; do
  if [ -e "$f" ] && grep -Eq "^($SECRET_NAMES)=" "$f"; then fail "$f holds a secret variable on the workstation"; fi
done

if [ "${#FAILURES[@]}" -eq 0 ]; then
  echo "PROD_SCRIPTS_OK"
  exit 0
fi
echo "PROD_SCRIPTS_BAD (${#FAILURES[@]}): ${FAILURES[*]}"
exit 1
