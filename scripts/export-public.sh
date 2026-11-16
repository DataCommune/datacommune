#!/usr/bin/env bash
# Export the public tree of DataCommune into a new repository with a single commit
# (publication audit of 2026-10-30, PUB-01 to PUB-04). Nothing is pushed, no remote is set.
#
#   bash scripts/export-public.sh [--with-tests] [--parent <repo> [--parent-branch <b>]]
#        [--branch <name>] [--message <subject>] <dest-dir> [<ref>]   # <ref>: HEAD by default
#
# --parent <repo> (a path or a URL, only read by `git fetch`): the exported commit is a child
# of <repo>'s branch --parent-branch (default master) instead of a root commit, so it reaches the
# public repository as a fast-forward, never a forced push (05-07 lot 7, D-135). Refused when
# the tree equals that parent's (nothing to publish). --branch names the branch of the exported
# repository (default master); --message gives the subject of the commit (scripts/publish-branch.sh).
#
# In order, each step stopping the export on failure:
#   1. <ref> resolves to a commit; <dest-dir> is absent or empty, outside this checkout.
#   2. `git archive` of that commit minus the exclusions of scripts/public-paths.sh, extracted into <dest-dir>.
#   3. Scans of the extracted tree: no match of the private patterns (generic ones below,
#      plus the personal ones of DC_EXPORT_PRIVATE_PATTERNS, a file kept out of every
#      repository because listing them here would publish them); no real address or sale
#      the fixtures were captured from (scripts/check-real-data.py with the private list
#      .planning/export/donnees-reelles.json read in <ref>, 05-07 lot 3); `gitleaks dir`
#      with the exported .gitleaks.toml exits 0.
#   4. `git init` (with --parent: the parent fetched, its tree as the index) and one commit
#      naming the source commit, by DC_EXPORT_AUTHOR_NAME
#      <DC_EXPORT_AUTHOR_EMAIL> at the date of the source commit, so the same inputs give
#      the same commit. Both variables are required and have no default: the author of the
#      public commits is a decision (D-138 of docs/DECISIONS.md), and no personal value is
#      written in the repository. A personal mailbox is refused.
#   5. The exported index equals the source tree minus the exclusions (path, mode, blob),
#      and `gitleaks git` reads exactly the 1 new commit and finds nothing.
#   6. With --with-tests, from the export: scripts/test-backend.ps1 (lint, contracts,
#      pytest without PostGIS), npm ci + scripts/test-frontend.ps1 -Gate all (gates,
#      check, vitest, build), scripts/inventory-check.py. No Docker. Logs in <dest-dir>.logs.
#
# Homelab host names and container numbers (pve02, CT 102/103/275) that remain outside the
# excluded paths are counted on INFO lines for the reviewer, not refused, as the files that
# still mention .planning/ (a test that reads it fails in step 6).
# Output: INFO lines, `EXPORT_OK dest=<dir> source=<sha> commit=<sha> files=<n> branch=<b>
# parent=<sha|none>`, then with
# --with-tests `EXPORT_TESTS backend=<PASS|FAIL> frontend=<…> inventory=<…> logs=<dir>`.
# Requires git, tar, grep, python and gitleaks (GITLEAKS, else PATH); pwsh, uv, npm for 6.
set -euo pipefail

# Paths left out of the public export and paths refused in it: scripts/public-paths.sh, shared
# with scripts/check-public-parity.sh.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=public-paths.sh
. "$here/public-paths.sh"

# Generic private patterns (extended regex, case-insensitive), the class at line end.
personal_mail='@(gmail|googlemail|hotmail|outlook|live|yahoo|icloud|proton|protonmail|free|orange|wanadoo|laposte|sfr|gmx)\.[a-z]{2,}'
generic_patterns=(
  '[A-Z]:[\\/]+Users[\\/]+[A-Za-z0-9._-]+'                                 # Windows profile path
  '/c/Users/[A-Za-z0-9._-]+'                                                # same, Git Bash form
  '(^|[^0-9.])10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)'             # RFC 1918
  '(^|[^0-9.])172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)' # RFC 1918
  '(^|[^0-9.])192\.168\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)'                   # RFC 1918
  '(^|[^0-9.])100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)' # CGNAT, Tailscale
  "$personal_mail" # personal mail
  '[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev'                                    # personal Cloudflare subdomain
  # A container id given a value (default, assignment, YAML key): machine values live in
  # git-ignored *.local.env files (D-140). 9xx stays free for the fictitious ids of the tests.
  '(ctid|ct_id)[a-z_]*\}?"?[[:space:]]*(:=|:-|=|:)[[:space:]]*"?[1-8][0-9]{2}([^0-9]|$)' # container id value
)

die() { echo "EXPORT_REFUSED: $*" >&2; exit 1; }
usage() {
  echo "usage: $0 [--with-tests] [--parent <repo> [--parent-branch <b>]] [--branch <name>]" \
    "[--message <subject>] <dest-dir> [<ref>]" >&2
  exit 2
}
with_tests=0 parent_repo="" parent_branch=master branch=master subject=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-tests) with_tests=1; shift ;;
    --parent) [ -n "${2:-}" ] || usage; parent_repo="$2"; shift 2 ;;
    --parent-branch) [ -n "${2:-}" ] || usage; parent_branch="$2"; shift 2 ;;
    --branch) [ -n "${2:-}" ] || usage; branch="$2"; shift 2 ;;
    --message) [ -n "${2:-}" ] || usage; subject="$2"; shift 2 ;;
    --) shift; break ;;
    -*) usage ;;
    *) break ;;
  esac
done
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || usage
git check-ref-format --branch "$branch" >/dev/null 2>&1 || die "not a branch name: $branch"

repo="$(cd "$(git rev-parse --show-toplevel)" && pwd)"
ref="${2:-HEAD}"
src="$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}")" || die "'$ref' is not a commit"
gitleaks="${GITLEAKS:-$(command -v gitleaks || true)}"
[ -n "$gitleaks" ] || die "gitleaks not found (set GITLEAKS or add it to PATH)"
private_patterns="${DC_EXPORT_PRIVATE_PATTERNS:-$HOME/.config/datacommune/export-private-patterns}"
[ -f "$private_patterns" ] ||
  die "personal patterns file missing: $private_patterns (one extended regex per line)"
command -v python >/dev/null || die "python not found (needed by the real-data check)"
# The list of the real values the fixtures were captured from: private, read in the exported
# commit itself so the check matches the tree it guards.
real_data_list=".planning/export/donnees-reelles.json"
git -C "$repo" cat-file -e "$src:$real_data_list" 2>/dev/null ||
  die "$real_data_list missing in $src: the real-data check would compare against nothing"
if [ "$with_tests" -eq 1 ]; then
  for tool in pwsh uv npm; do
    command -v "$tool" >/dev/null || die "$tool not found (needed by --with-tests)"
  done
fi
author_name="${DC_EXPORT_AUTHOR_NAME:-}"
author_email="${DC_EXPORT_AUTHOR_EMAIL:-}"
[ -n "$author_name" ] && [ -n "$author_email" ] ||
  die "set DC_EXPORT_AUTHOR_NAME and DC_EXPORT_AUTHOR_EMAIL (author of the public commit, D-138)"
if printf '%s
' "$author_email" | grep -q -i -E "$personal_mail"; then
  die "DC_EXPORT_AUTHOR_EMAIL is a personal mailbox; use the GitHub noreply address"
fi

parent="$(cd "$(dirname "$1")" 2>/dev/null && pwd)" || die "parent directory of '$1' does not exist"
dest="$parent/$(basename "$1")"
case "$dest/" in "$repo"/*) die "destination is inside the checkout: $dest" ;; esac
if [ -e "$dest" ]; then
  [ -d "$dest" ] && [ -z "$(ls -A "$dest")" ] || die "destination is not an empty directory: $dest"
else
  mkdir "$dest"
fi

# --- 2. archive minus exclusions ----------------------------------------------------------
public_pathspecs
git -C "$repo" -c core.autocrlf=false archive --format=tar "$src" -- . "${pathspecs[@]}" |
  tar -x -C "$dest"
for private_path in "${public_forbidden_paths[@]}"; do
  [ ! -e "$dest/$private_path" ] || die "$private_path is present in the export"
done

# --- 3. scans of the extracted tree ---------------------------------------------------------
patterns_file="$(mktemp)"
real_data_file="$(mktemp)"
trap 'rm -f "$patterns_file" "$real_data_file"' EXIT
{ printf '%s\n' "${generic_patterns[@]}"; grep -v -E '^[[:space:]]*(#|$)' "$private_patterns" | tr -d '\r'; } >"$patterns_file"
status=0
matches="$(grep -r -n -I -i -E -f "$patterns_file" "$dest")" || status=$?
[ "$status" -le 1 ] || die "pattern scan failed (grep exit $status)"
if [ "$status" -eq 0 ]; then
  printf '%s\n' "$matches" | sed "s#^$dest/##" | cut -c1-200 >&2
  die "private patterns found in the exported tree ($(printf '%s\n' "$matches" | wc -l) lines)"
fi
git -C "$repo" show "$src:$real_data_list" >"$real_data_file"
real_data_log="$(python "$repo/scripts/check-real-data.py" "$real_data_file" "$dest")" || {
  printf '%s
' "$real_data_log" | head -20 >&2
  die "real data of the captured fixtures found in the exported tree (values not printed)"
}
echo "INFO $(printf '%s
' "$real_data_log" | tail -1)"
(cd "$dest" && "$gitleaks" dir . --config .gitleaks.toml --redact --no-banner --log-level warn) ||
  die "gitleaks dir reported findings in the exported tree"

for name in 'pve0[0-9]' '\bCT ?[0-9]{3}\b|ct/[0-9]{3}'; do
  echo "INFO kept homelab name /$name/: $(grep -r -l -I -E "$name" "$dest" | wc -l) files"
done
echo "INFO files mentioning .planning/: $(grep -r -l -I -F '.planning/' "$dest" | wc -l)"

# --- 4. new repository, one commit -----------------------------------------------------------
src_date="$(git -C "$repo" log -1 --format=%cI "$src")"
git -C "$dest" init -q -b "$branch"
parent_sha=""
if [ -n "$parent_repo" ]; then
  git -C "$dest" fetch -q --no-tags "$parent_repo" "refs/heads/$parent_branch" ||
    die "cannot read the branch $parent_branch of $parent_repo"
  parent_sha="$(git -C "$dest" rev-parse FETCH_HEAD)"
  git -C "$dest" update-ref "refs/heads/$branch" "$parent_sha"
  # The parent's tree as the index: `add -A` below then records additions, changes and removals.
  git -C "$dest" read-tree "$parent_sha"
fi
# --force: every file here comes from the archive, tracked files the .gitignore matches
# included (api/.env.public.example).
git -C "$dest" -c core.autocrlf=false add -A --force
# A Windows checkout carries no executable bit: copy it from the source tree.
exec_paths=()
while IFS= read -r -d '' entry; do
  [ "${entry%% *}" = 100755 ] && exec_paths+=("${entry#*$'\t'}")
done < <(git -C "$repo" ls-tree -r -z "$src")
for path in "${exec_paths[@]}"; do
  [ -f "$dest/$path" ] && git -C "$dest" update-index --chmod=+x -- "$path"
done
if [ -n "$parent_sha" ] && git -C "$dest" diff --cached --quiet; then
  die "nothing to publish: the public tree of $src equals $parent_branch of $parent_repo"
fi
if [ -z "$parent_sha" ]; then
  message="${subject:-DataCommune : publication initiale du code source (AGPL-3.0-or-later)}

Arbre du dépôt de travail privé au commit $src ($src_date),
exporté sans historique par scripts/export-public.sh (chemins exclus : liste
de scripts/public-paths.sh). Provenance du code : voir README.md."
else
  message="${subject:-DataCommune : code source au commit $src}

Arbre du dépôt de travail privé au commit $src ($src_date),
exporté par scripts/export-public.sh au-dessus de $parent_branch ($parent_sha)."
fi
GIT_AUTHOR_NAME="$author_name" GIT_AUTHOR_EMAIL="$author_email" GIT_AUTHOR_DATE="$src_date" \
  GIT_COMMITTER_NAME="$author_name" GIT_COMMITTER_EMAIL="$author_email" \
  GIT_COMMITTER_DATE="$src_date" git -C "$dest" commit -q -m "$message"

# --- 5. fidelity and history scan ------------------------------------------------------------
source_index="$(git -C "$repo" rev-parse --absolute-git-dir)/export-public.index"
trap 'rm -f "$patterns_file" "$real_data_file" "$source_index"' EXIT
GIT_INDEX_FILE="$source_index" git -C "$repo" read-tree "$src"
expected="$(GIT_INDEX_FILE="$source_index" git -C "$repo" ls-files -s -- . "${pathspecs[@]}")"
actual="$(git -C "$dest" ls-files -s)"
if [ "$expected" != "$actual" ]; then
  { diff <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") || true; } | head -20 >&2
  die "exported index differs from the source tree minus the exclusions"
fi
# The wrapper refuses a scan that read no commit (gitleaks alone exits 0 on an empty log).
scan_range=()
[ -z "$parent_sha" ] || scan_range=(--log-opts="$parent_sha..HEAD")
scan_log="$(cd "$dest" && GITLEAKS_BIN="$gitleaks" bash "$repo/scripts/secret-scan-commits.sh" \
  --config .gitleaks.toml "${scan_range[@]}" 2>&1)" ||
  { printf '%s
' "$scan_log" | tail -5 >&2; die "gitleaks git failed on the new repository"; }
printf '%s
' "$scan_log" | grep -q -x 'SECRET_SCAN_COMMITS=1' ||
  die "gitleaks git did not scan exactly the 1 new commit"
echo "INFO author: $author_name <$author_email>"
echo "INFO gitleaks git: 1 commits scanned, 0 findings"

echo "EXPORT_OK dest=$dest source=$src commit=$(git -C "$dest" rev-parse HEAD)" \
  "files=$(printf '%s\n' "$actual" | wc -l) branch=$branch parent=${parent_sha:-none}"
[ "$with_tests" -eq 1 ] || exit 0

# --- 6. project suites from the export ---------------------------------------------------------
logs="$dest.logs"
mkdir -p "$logs"
results="" failed=0
run_suite() { # <label> <command...>, run from the export
  local label="$1"; shift
  if (cd "$dest" && "$@") >"$logs/$label.log" 2>&1; then
    results="$results $label=PASS"
  else
    results="$results $label=FAIL"; failed=1
  fi
}
run_suite backend pwsh -NoProfile -File scripts/test-backend.ps1
run_suite frontend bash -c 'npm --prefix frontend ci --no-audit --no-fund &&
  pwsh -NoProfile -File scripts/test-frontend.ps1 -Gate all'
run_suite inventory python scripts/inventory-check.py docs/INVENTORY-3394395a.md
echo "EXPORT_TESTS${results} logs=$logs"
exit "$failed"
