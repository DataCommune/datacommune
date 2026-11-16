#!/usr/bin/env bash
# Parity of the two repositories (D-135): the tree of the private master minus the private paths
# of scripts/public-paths.sh must equal the tree of the public master, path by path, mode and blob.
#
#   bash scripts/check-public-parity.sh [<private ref>] [<public ref>]
#        # defaults: master and refs/remotes/public/master (`git fetch public` first)
#
# Run in the private working repository. Reads only; writes two temporary index files in .git.
# Output: `PARITY_OK files=<n>`, or one `PARITY_DIFF <path>` line per path that differs (present
# on one side only, or another mode or content), then `PARITY_GAPS <n>`.
# Exit 0 equal | 1 gaps | 2 usage or unknown ref.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=public-paths.sh
. "$here/public-paths.sh"

[ "$#" -le 2 ] || { echo "usage: $0 [<private ref>] [<public ref>]" >&2; exit 2; }
private_ref="${1:-master}"
public_ref="${2:-refs/remotes/public/master}"
repo="$(git rev-parse --show-toplevel)"
private_sha="$(git -C "$repo" rev-parse --verify --quiet "$private_ref^{commit}")" ||
  { echo "unknown private ref: $private_ref" >&2; exit 2; }
public_sha="$(git -C "$repo" rev-parse --verify --quiet "$public_ref^{commit}")" ||
  { echo "unknown public ref: $public_ref (git fetch public)" >&2; exit 2; }

gitdir="$(git -C "$repo" rev-parse --absolute-git-dir)"
private_index="$gitdir/parity-private.index" public_index="$gitdir/parity-public.index"
trap 'rm -f "$private_index" "$public_index"' EXIT
public_pathspecs
GIT_INDEX_FILE="$private_index" git -C "$repo" read-tree "$private_sha"
GIT_INDEX_FILE="$public_index" git -C "$repo" read-tree "$public_sha"
expected="$(GIT_INDEX_FILE="$private_index" git -C "$repo" ls-files -s -- . "${pathspecs[@]}")"
actual="$(GIT_INDEX_FILE="$public_index" git -C "$repo" ls-files -s)"

# A path that differs shows on one side or both: list each once.
gaps="$({ diff <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") || true; } |
  sed -n -E 's/^[<>] [0-7]+ [0-9a-f]+ [0-9]+\t//p' | sort -u)"
if [ -z "$gaps" ]; then
  echo "PARITY_OK files=$(printf '%s\n' "$actual" | grep -c .) private=${private_sha:0:12} public=${public_sha:0:12}"
  exit 0
fi
printf '%s\n' "$gaps" | sed 's/^/PARITY_DIFF /'
echo "PARITY_GAPS $(printf '%s\n' "$gaps" | grep -c .) private=${private_sha:0:12} public=${public_sha:0:12}"
exit 1
