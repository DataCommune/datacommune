#!/usr/bin/env bash
# DCO check of a pull request (D-136, CONTRIBUTING.md; run by .github/workflows/dco.yml): every
# commit of <base>..<head>, merges aside, carries a `Signed-off-by: Name <address>` trailer line.
#
#   bash scripts/check-signed-off.sh <base sha> <head sha>
#
# Output: one `DCO_MISSING <sha12> <subject>` line per unsigned commit, then `DCO_OK commits=<n>`
# or `DCO_FAILED <k> of <n>`. Exit 0 all signed | 1 one unsigned, or no commit in the range (a
# check of nothing must not pass) | 2 usage or unknown commit.
set -euo pipefail

[ "$#" -eq 2 ] || { echo "usage: $0 <base sha> <head sha>" >&2; exit 2; }
for sha in "$1" "$2"; do
  git rev-parse --verify --quiet "$sha^{commit}" >/dev/null || { echo "unknown commit: $sha" >&2; exit 2; }
done

commits="$(git rev-list --no-merges "$1..$2")"
total="$(printf '%s' "$commits" | grep -c . || true)"
[ "$total" -gt 0 ] || { echo "DCO_FAILED: no commit in $1..$2" >&2; exit 1; }
missing=0
while IFS= read -r sha; do
  if ! git log -1 --format='%(trailers:key=Signed-off-by,valueonly)' "$sha" | grep -q -E '^.+ <[^<>@ ]+@[^<> ]+>$'; then
    echo "DCO_MISSING ${sha:0:12} $(git log -1 --format=%s "$sha")"
    missing=$((missing + 1))
  fi
done <<<"$commits"
if [ "$missing" -gt 0 ]; then
  echo "DCO_FAILED $missing of $total: sign each commit with \`git commit -s\` (CONTRIBUTING.md)"
  exit 1
fi
echo "DCO_OK commits=$total"
