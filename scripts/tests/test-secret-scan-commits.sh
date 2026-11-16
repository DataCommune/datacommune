#!/usr/bin/env bash
# Test of scripts/secret-scan-commits.sh: a history scan that read no commit
# must fail, even though gitleaks itself exits 0 with "no leaks found".
#
# Needs git and gitleaks on PATH (or GITLEAKS_BIN). No network, no Docker;
# everything happens in a throwaway repository.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/../secret-scan-commits.sh"
gitleaks_bin="${GITLEAKS_BIN:-gitleaks}"

command -v "$gitleaks_bin" >/dev/null 2>&1 || {
  echo "FAIL gitleaks not found (set GITLEAKS_BIN): the guard cannot be tested" >&2
  exit 2
}

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
failures=0

check() { # <name> <expected exit code> <actual exit code>
  if [ "$2" = "$3" ]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected exit $2, got $3"
    failures=$((failures + 1))
  fi
}

repo="$work/repo"
git init -q "$repo"
git -C "$repo" -c user.name=test -c user.email=test@example.invalid \
  commit -q --allow-empty -m "root"
echo "plain text" > "$repo/file.txt"
git -C "$repo" add file.txt
git -C "$repo" -c user.name=test -c user.email=test@example.invalid \
  commit -q -m "one file"

# 1. A healthy history: commits are read, nothing is found.
(cd "$repo" && bash "$guard" >"$work/ok.log" 2>&1)
check "healthy history passes" 0 $?
grep -Eq '[1-9][0-9]* commits scanned' "$work/ok.log"
check "healthy history reports its commits" 0 $?

# 2. The blind scan of 2026-11-10: a ref that names a missing object makes
#    `git log --all` fail, gitleaks reads an empty log and reports success.
printf '%s\n' "0123456789abcdef0123456789abcdef01234567" \
  > "$repo/.git/refs/heads/master (1)"
(cd "$repo" && "$gitleaks_bin" git . --no-banner --redact --exit-code 1 \
  --log-opts="--all" >"$work/raw.log" 2>&1)
check "gitleaks alone accepts the blind scan (the defect)" 0 $?
grep -q '0 commits scanned' "$work/raw.log"
check "gitleaks alone reports 0 commits" 0 $?

(cd "$repo" && bash "$guard" --log-opts="--all" >"$work/blind.log" 2>&1)
check "guard refuses a scan of 0 commits" 1 $?
grep -q 'SECRET_SCAN_BLIND' "$work/blind.log"
check "guard names the refusal" 0 $?
rm -f "$repo/.git/refs/heads/master (1)"

# 3. A range that does not exist (first push of a root commit) is blind too.
(cd "$repo" && bash "$guard" --log-opts="HEAD~9..HEAD" >"$work/range.log" 2>&1)
check "guard refuses an unresolvable range" 1 $?

# 4. A real finding still fails, with gitleaks' own exit code.
printf 'aws_secret_access_key = "%s%s"\n' "wJalrXUtnFEMI/K7MDENG/" "bPxRfiCYzT9qLmN2pQr5" \
  > "$repo/leak.txt"
git -C "$repo" add leak.txt
git -C "$repo" -c user.name=test -c user.email=test@example.invalid \
  commit -q -m "fabricated key"
(cd "$repo" && bash "$guard" >"$work/leak.log" 2>&1)
rc=$?
if [ "$rc" -ne 0 ]; then echo "ok   a finding fails the scan (exit $rc)"; else
  echo "FAIL a finding must fail the scan"; failures=$((failures + 1)); fi

if [ "$failures" -ne 0 ]; then
  echo "SECRET_SCAN_GUARD_TEST FAIL ($failures)"
  exit 1
fi
echo "SECRET_SCAN_GUARD_TEST PASS"
