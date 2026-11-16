#!/usr/bin/env bash
# Private bootstrap of /opt/datacommune/.env on the rented server (Ianis's gesture, plan
# 04.2-01 task 1 step 5). Run ON the server, as root, from a terminal:
#
#   scp infra/prod/env-bootstrap.sh <admin>@<ip>:/tmp/env-bootstrap.sh
#   ssh -t <admin>@<ip> 'sudo bash /tmp/env-bootstrap.sh; rm -f /tmp/env-bootstrap.sh'
#
# (The file is copied first because a script fed on ssh's stdin would leave no terminal
# for sudo's password nor for the token prompt.) Without any terminal, the token comes from
# a dedicated private channel instead: a 0600 file redirected to stdin, destroyed right after:
#   sudo bash /tmp/env-bootstrap.sh --token-stdin < /tmp/<0600 file>; shred -u /tmp/<0600 file>
#
# What it does, with umask 077:
#   - creates $ENV_FILE with `install -m 600 -o datacommune -g datacommune /dev/null` if it
#     is missing; an existing file keeps its values, only its mode is set back to 600;
#   - adds, only when the name is absent: POSTGRES_PASSWORD, JWT_SECRET_KEY,
#     API_KEY_HMAC_SECRET (openssl rand -hex 32) and ADMIN_PASSWORD (-hex 24), generated
#     here, plus POSTGRES_DB, POSTGRES_USER, DEBUG, APP_ENVIRONMENT, EMAIL_ENABLED,
#     STRIPE_ENABLED (not secret);
#   - CF_TUNNEL_TOKEN: read without echo from /dev/tty (or stdin with --token-stdin), added
#     once; a token already present is never replaced (rotation: by hand, README);
#   - never PUBLIC_DC_API_KEY: the real dc_anon_ key is created by deploy.sh at the first
#     deployment, from the base it lives in.
# Prints names and counts only, never a value. Last line: ENV_BOOTSTRAP_OK ...
# Overrides for the local test only: ENV_FILE, ENV_OWNER (empty = no chown).
set -euo pipefail
umask 077

ENV_FILE="${ENV_FILE:-/opt/datacommune/.env}"
ENV_OWNER="${ENV_OWNER-datacommune}"
token_stdin=0
case "${1:-}" in
  "") ;;
  --token-stdin) token_stdin=1 ;;
  *) echo "usage: $0 [--token-stdin]" >&2; exit 2 ;;
esac
refuse() { echo "REFUSED: $*" >&2; exit 80; }

[ -d "$(dirname "$ENV_FILE")" ] || refuse "$(dirname "$ENV_FILE") missing: run provision.sh first"
if [ ! -e "$ENV_FILE" ]; then
  if [ -n "$ENV_OWNER" ]; then
    install -m 600 -o "$ENV_OWNER" -g "$ENV_OWNER" /dev/null "$ENV_FILE"
  else
    install -m 600 /dev/null "$ENV_FILE"
  fi
  echo "created: $ENV_FILE"
fi
chmod 600 "$ENV_FILE"
# An existing file whose last line has no newline would glue the next name onto it.
[ -z "$(tail -c1 "$ENV_FILE")" ] || printf '\n' >> "$ENV_FILE"

has() { grep -q "^$1=" "$ENV_FILE"; }
# printf is a builtin: generated values never appear in a process argument list.
add_secret() { # add_secret <name> <bytes>
  if has "$1"; then echo "kept: $1"; return 0; fi
  printf '%s=%s\n' "$1" "$(openssl rand -hex "$2")" >> "$ENV_FILE"
  echo "added: $1"
}
add_plain() { # add_plain <name> <value>
  if has "$1"; then echo "kept: $1"; return 0; fi
  printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
  echo "added: $1"
}

add_secret POSTGRES_PASSWORD 32
add_secret JWT_SECRET_KEY 32
add_secret API_KEY_HMAC_SECRET 32
add_secret ADMIN_PASSWORD 24
add_plain POSTGRES_DB datacommune
add_plain POSTGRES_USER datacommune
add_plain DEBUG false
add_plain APP_ENVIRONMENT production
add_plain EMAIL_ENABLED false
add_plain STRIPE_ENABLED false

if has CF_TUNNEL_TOKEN; then
  echo "kept: CF_TUNNEL_TOKEN (already present, never replaced; nothing read)"
else
  CF_TUNNEL_TOKEN=""
  if [ "$token_stdin" = 1 ]; then
    IFS= read -r CF_TUNNEL_TOKEN || true
  else
    { printf 'CF_TUNNEL_TOKEN (input hidden): ' > /dev/tty; } 2>/dev/null ||
      refuse "no terminal: rerun with ssh -t, or use --token-stdin with a private 0600 file"
    IFS= read -rs CF_TUNNEL_TOKEN < /dev/tty || true
    printf '\n' > /dev/tty
  fi
  [ -n "$CF_TUNNEL_TOKEN" ] || refuse "empty CF_TUNNEL_TOKEN: nothing added"
  case "$CF_TUNNEL_TOKEN" in
    *[!A-Za-z0-9_=+/.-]*) unset CF_TUNNEL_TOKEN; refuse "CF_TUNNEL_TOKEN has unexpected characters: nothing added" ;;
  esac
  printf 'CF_TUNNEL_TOKEN=%s\n' "$CF_TUNNEL_TOKEN" >> "$ENV_FILE"
  unset CF_TUNNEL_TOKEN
  echo "added: CF_TUNNEL_TOKEN"
fi

names="$(cut -d= -f1 "$ENV_FILE" | grep . | sort | tr '\n' ' ')"
echo "ENV_BOOTSTRAP_OK mode_owner=$(stat -c '%a %U' "$ENV_FILE" | tr ' ' ':') lines=$(grep -c . "$ENV_FILE") names=${names% }"
