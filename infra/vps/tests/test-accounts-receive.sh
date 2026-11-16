#!/usr/bin/env bash
# infra/vps/accounts-receive.sh (plan 05-01, case e), on a temporary directory.
# Prints ACCOUNTS_RECEIVE_OK only when every case passes.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECV="${ACCOUNTS_RECEIVE_SCRIPT:-$HERE/../accounts-receive.sh}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export DC_ACCOUNTS_DIR="$WORK/srv"
mkdir -p "$DC_ACCOUNTS_DIR"
FAILS=0
fail() { echo "FAIL $*"; FAILS=$((FAILS + 1)); }
pass() { echo "PASS $*"; }

recv() { # recv <name> <input file>: stdout in $WORK/out, rc in RC
  RC=0
  SSH_ORIGINAL_COMMAND="$1" bash "$RECV" <"$2" >"$WORK/out" 2>"$WORK/err" || RC=$?
}
listing() { (cd "$DC_ACCOUNTS_DIR" && ls -A | sort | tr '\n' ' '); }
printf 'age-encryption.org/v1\nciphertext\n' >"$WORK/good"
: >"$WORK/empty"
printf 'not encrypted\n' >"$WORK/clear"

recv accounts-20261104T031500Z.tar.age "$WORK/good"
if [ "$RC" = 0 ] && grep -qx "RECEIVED accounts-20261104T031500Z.tar.age bytes=$(wc -c <"$WORK/good" | tr -d ' ') kept=1" "$WORK/out" &&
  cmp -s "$DC_ACCOUNTS_DIR/accounts-20261104T031500Z.tar.age" "$WORK/good" &&
  [ "$(listing)" = "accounts-20261104T031500Z.tar.age " ]; then pass "(e) nominal -> RECEIVED, stored, no partial left"
else fail "(e) nominal rc=$RC $(cat "$WORK/out") [$(listing)]"; fi

refused() { # refused <label> <name> <input>
  local before; before="$(listing)"
  recv "$2" "$3"
  if [ "$RC" = 2 ] && grep -q '^REFUSED ' "$WORK/out" && [ "$(listing)" = "$before" ]; then pass "(e) $1 -> REFUSED, nothing written"
  else fail "(e) $1 rc=$RC $(cat "$WORK/out") [$(listing)]"; fi
}
refused "odd name" "accounts-2026.tar.age" "$WORK/good"
refused "path in the name" "../accounts-20261104T031500Z.tar.age" "$WORK/good"
refused "command injection" "accounts-20261104T031500Z.tar.age; rm -rf /" "$WORK/good"
refused "empty input" "accounts-20261105T031500Z.tar.age" "$WORK/empty"
refused "no age header" "accounts-20261105T031500Z.tar.age" "$WORK/clear"

# Retention: 32 archives received -> the 30 newest stay; an unrelated file is untouched.
: >"$DC_ACCOUNTS_DIR/keep-me.txt"
for d in $(seq -w 1 32); do
  recv "accounts-202612${d}T031500Z.tar.age" "$WORK/good" 2>/dev/null || true
done
n="$(find "$DC_ACCOUNTS_DIR" -name 'accounts-*.tar.age' | wc -l | tr -d ' ')"
if [ "$n" = 30 ] && [ ! -e "$DC_ACCOUNTS_DIR/accounts-20261201T031500Z.tar.age" ] &&
  [ ! -e "$DC_ACCOUNTS_DIR/accounts-20261104T031500Z.tar.age" ] &&
  [ -e "$DC_ACCOUNTS_DIR/accounts-20261232T031500Z.tar.age" ] && [ -e "$DC_ACCOUNTS_DIR/keep-me.txt" ] &&
  grep -q 'kept=30$' "$WORK/out"; then pass "(e) retention keeps the 30 newest, other files untouched"
else fail "(e) retention: $n archives [$(listing)]"; fi

bash -n "$RECV" && pass "bash -n accounts-receive.sh" || fail "bash -n accounts-receive.sh"
if [ "$FAILS" = 0 ]; then echo ACCOUNTS_RECEIVE_OK; else echo "$FAILS case(s) failed"; exit 1; fi
