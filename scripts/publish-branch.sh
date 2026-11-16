#!/usr/bin/env bash
# Publish a private branch as ONE commit on a branch of the public repository (D-135, circuit of
# the public repository framing note of 2026-11-10, § 2.2; 05-07 lot 7).
#
#   bash scripts/publish-branch.sh [--public <repo>] [--message <subject>] [--push] \
#        <type>/<subject> [<ref>]
#
# <type>/<subject>: the public branch, `feat|fix|data|docs|infra|chore` then lowercase words
# joined by hyphens (cadrage § 4); anything else, `claude/*` included, is refused (2).
# <ref>: the private commit to publish (default HEAD). --public: the public repository, a path
# or a URL (default: the URL of the remote `public`). --message: the subject of the public
# commit (default: the subject of <ref>).
#
# The tree of <ref> goes through scripts/export-public.sh (private paths left out, private
# patterns, real data, gitleaks, fidelity): one commit, child of the public branch when it already
# exists (a fast-forward update of an open pull request), else of the public master. It is written
# to a new repository under DC_PUBLISH_DIR (default: a temporary directory) and pushed only with
# --push, never forced: pushing to the real public repository is the maintainer's gesture.
# Output: the export lines, then `PUBLISH_READY dir=<dir> branch=<b> commit=<sha>` and the push
# command, or with --push `PUBLISH_PUSHED branch=<b> commit=<sha>`.
# Exit 0 | 1 refused by the export or the push | 2 usage.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
usage() {
  echo "usage: $0 [--public <repo>] [--message <subject>] [--push] <type>/<subject> [<ref>]" >&2
  exit 2
}
public="" subject="" push=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --public) [ -n "${2:-}" ] || usage; public="$2"; shift 2 ;;
    --message) [ -n "${2:-}" ] || usage; subject="$2"; shift 2 ;;
    --push) push=1; shift ;;
    -*) usage ;;
    *) break ;;
  esac
done
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || usage
name="$1" ref="${2:-HEAD}"
[[ "$name" =~ ^(feat|fix|data|docs|infra|chore)/[a-z0-9]+(-[a-z0-9]+)*$ ]] || {
  echo "PUBLISH_REFUSED: '$name' is not <feat|fix|data|docs|infra|chore>/<lowercase-words>" >&2
  exit 2
}
git rev-parse --verify --quiet "$ref^{commit}" >/dev/null || { echo "unknown commit: $ref" >&2; exit 2; }
if [ -z "$public" ]; then
  public="$(git remote get-url public 2>/dev/null)" ||
    { echo "no --public and no remote 'public' (git remote add public <url>)" >&2; exit 2; }
fi
[ -n "$subject" ] || subject="$(git log -1 --format=%s "$ref")"

# An open pull request keeps its branch: the new commit goes on top of it (fast-forward).
parent_branch=master
if git ls-remote --exit-code --heads "$public" "refs/heads/$name" >/dev/null 2>&1; then
  parent_branch="$name"
fi
base="${DC_PUBLISH_DIR:-$(mktemp -d)}"
dir="$base/publish-${name//\//-}"
bash "$here/export-public.sh" --parent "$public" --parent-branch "$parent_branch" \
  --branch "$name" --message "$subject" "$dir" "$ref"
commit="$(git -C "$dir" rev-parse HEAD)"
if [ "$push" -eq 0 ]; then
  echo "PUBLISH_READY dir=$dir branch=$name commit=$commit"
  echo "push: git -C '$dir' push '$public' '$name:refs/heads/$name'"
  exit 0
fi
git -C "$dir" push -q "$public" "$name:refs/heads/$name" || { echo "PUBLISH_REFUSED: push failed" >&2; exit 1; }
echo "PUBLISH_PUSHED branch=$name commit=$commit"
