#!/usr/bin/env bash
# Test of scripts/check-signed-off.sh, the DCO check of .github/workflows/dco.yml: a pull request
# passes only when each of its commits (merges aside) carries a Signed-off-by line; an empty range
# is refused (a check of nothing must not pass). Throwaway repository, invented identities.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
check_script="$here/../check-signed-off.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
failures=0
check() { # <name> <expected exit code> <actual exit code>
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected exit $2, got $3"; failures=$((failures + 1)); fi
}
gitc() { git -C "$work/r" -c user.name="Contributor Test" -c user.email=contributor@example.invalid "$@"; }

git init -q -b master "$work/r"
gitc commit -q --allow-empty -m "root"
base="$(gitc rev-parse HEAD)"
gitc checkout -q -b feat/x
gitc commit -q --allow-empty -s -m "feat: signed"
signed="$(gitc rev-parse HEAD)"

(cd "$work/r" && bash "$check_script" "$base" "$signed" ) >"$work/1.log" 2>&1
check "signed pull request passes" 0 $?

gitc commit -q --allow-empty -m "fix: not signed"
unsigned="$(gitc rev-parse HEAD)"
(cd "$work/r" && bash "$check_script" "$base" "$unsigned" ) >"$work/2.log" 2>&1
check "an unsigned commit fails the pull request" 1 $?
grep -q "^DCO_MISSING ${unsigned:0:12} " "$work/2.log"
check "the unsigned commit is named" 0 $?

gitc commit -q --allow-empty -m "docs: a signature in the middle of a line Signed-off-by: x <x@example.invalid>"
(cd "$work/r" && bash "$check_script" "$unsigned" "$(gitc rev-parse HEAD)" ) >"$work/3.log" 2>&1
check "a Signed-off-by that is not a trailer line does not count" 1 $?

gitc checkout -q master
gitc commit -q --allow-empty -s -m "chore: on master"
gitc checkout -q feat/x
gitc reset -q --hard "$signed"
gitc merge -q --no-ff -m "merge master" master
(cd "$work/r" && bash "$check_script" "$base" "$(gitc rev-parse HEAD)" ) >"$work/4.log" 2>&1
check "an unsigned merge commit is not required to sign" 0 $?

(cd "$work/r" && bash "$check_script" "$signed" "$signed" ) >"$work/5.log" 2>&1
check "an empty range is refused" 1 $?
(cd "$work/r" && bash "$check_script" "$base" ) >"$work/6.log" 2>&1
check "a missing SHA is refused" 2 $?

if [ "$failures" -eq 0 ]; then echo "DCO_CHECK_TEST PASS"; else echo "DCO_CHECK_TEST FAIL ($failures)"; exit 1; fi
