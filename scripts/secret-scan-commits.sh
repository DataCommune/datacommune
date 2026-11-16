#!/usr/bin/env bash
# History secret scan: `gitleaks git .` on the current repository.
#
# Usage: bash scripts/secret-scan-commits.sh [gitleaks git options...]
#   e.g. bash scripts/secret-scan-commits.sh --config .gitleaks.toml --log-opts="A..B"
#
# Output is always redacted. GITLEAKS_BIN overrides the binary.
set -uo pipefail

gitleaks_bin="${GITLEAKS_BIN:-gitleaks}"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

"$gitleaks_bin" git . --redact --no-banner --exit-code 1 "$@" 2>&1 | tee "$log"
rc="${PIPESTATUS[0]}"
[ "$rc" -eq 0 ] || exit "$rc"

# gitleaks reads `git log` and treats a failed or empty log as a clean scan:
# "0 commits scanned", "no leaks found", exit 0. On 2026-11-10 one broken ref
# made `git log --all` fail and the whole history went unread that way. A scan
# that read nothing proves nothing, so it fails here.
scanned="$(sed -n -E 's/.*[^0-9]([0-9]+) commits scanned.*/\1/p' "$log" | tail -n 1)"
if [ -z "$scanned" ] || [ "$scanned" -eq 0 ]; then
  echo "SECRET_SCAN_BLIND: gitleaks scanned ${scanned:-an unknown number of} commits." >&2
  echo "Check that the git log it reads works: git log --all >/dev/null; echo \$?" >&2
  exit 1
fi
echo "SECRET_SCAN_COMMITS=$scanned"
