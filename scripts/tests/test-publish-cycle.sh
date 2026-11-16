#!/usr/bin/env bash
# Test of the publication circuit of the two repositories (D-135, 05-07 lot 7), end to end on
# throwaway repositories: a private working repository P and a local bare repository playing the
# public one. Nothing leaves the machine; no real remote is read or written.
#
#   1. export-public.sh: the first public commit, pushed to the bare repository (the opening).
#   2. export-public.sh --parent: the next export is a child of the public master (fast-forward).
#   3. publish-branch.sh: a private branch becomes ONE public commit on <type>/<subject>, above the
#      public master; a name outside the convention is refused.
#   4. the pull request is squash-merged on the public master (as GitHub would), then P merges its
#      branch and `git merge public/master` without conflict; check-public-parity.sh finds 0 gap.
#   5. a private pattern injected in a branch makes the publication refuse, the public repository
#      unchanged; an unpublished change on the private master makes the parity check fail.
#
# Needs git, gitleaks (GITLEAKS, else PATH) and python. Every value here is invented.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$(cd "$here/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
failures=0

check() { # <name> <expected exit code> <actual exit code>
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected exit $2, got $3"; failures=$((failures + 1)); fi
}
yes_() { # <name> <command...>: the command succeeds
  if "${@:2}"; then echo "ok   $1"; else echo "FAIL $1"; failures=$((failures + 1)); fi
}

export DC_EXPORT_AUTHOR_NAME="Publication Test" DC_EXPORT_AUTHOR_EMAIL="publication-test@users.noreply.github.com"
export DC_EXPORT_PRIVATE_PATTERNS="$work/patterns"
printf 'zz-private-host\n' >"$work/patterns"
gitc() { git -c user.name=test -c user.email=test@example.invalid -c core.autocrlf=false "$@"; }

# --- the private repository ------------------------------------------------------------------------
P="$work/private"
mkdir -p "$P/scripts" "$P/app" "$P/.planning/export" "$P/infra/pve02"
for f in export-public.sh public-paths.sh publish-branch.sh check-public-parity.sh check-real-data.py \
  secret-scan-commits.sh; do
  cp "$src/$f" "$P/scripts/" 2>/dev/null || echo "missing scripts/$f" >&2
done
cp "$src/../.gitleaks.toml" "$P/.gitleaks.toml"
printf 'DataCommune test tree\n' >"$P/README.md"
printf 'version 1\n' >"$P/app/main.txt"
printf 'private plan\n' >"$P/.planning/STATE.md"
printf '{"chaines": ["99999_zz99_"], "ventes": [{"date": "2024-03-14", "prix": 123457}]}\n' \
  >"$P/.planning/export/donnees-reelles.json"
printf 'HOST=zz-private-host\n' >"$P/infra/pve02/host.env"
printf 'private agent notes\n' >"$P/CLAUDE.md"
gitc init -q -b master "$P"
gitc -C "$P" add -A && gitc -C "$P" commit -q -m "private root"

PUB="$work/public.git"
git init -q --bare -b master "$PUB"

# --- 1. opening: the first public commit ------------------------------------------------------------
(cd "$P" && bash scripts/export-public.sh "$work/export-1" >"$work/e1.log" 2>&1)
check "first export" 0 $?
git -C "$work/export-1" push -q "$PUB" master
check "first export pushed to the public repository" 0 $?
yes_ "private paths absent from the public tree" test -z "$(git -C "$PUB" ls-tree -r --name-only master | grep -E '^(\.planning|infra/pve02|CLAUDE\.md)')"
(cd "$P" && gitc remote add public "$PUB" && gitc fetch -q public && gitc merge -q --allow-unrelated-histories -m "merge public master" public/master)
check "private master takes the public root" 0 $?
(cd "$P" && bash scripts/check-public-parity.sh master public/master >"$work/p0.log" 2>&1)
check "parity after the opening" 0 $?
yes_ "parity names 0 gap" grep -q '^PARITY_OK' "$work/p0.log"

# --- 2. export --parent: a child of the public master -------------------------------------------------
printf 'version 2\n' >"$P/app/main.txt"; gitc -C "$P" commit -q -am "private work"
(cd "$P" && bash scripts/export-public.sh --parent "$PUB" "$work/export-2" >"$work/e2.log" 2>&1)
check "export --parent" 0 $?
yes_ "export --parent: parent is the public master" test "$(git -C "$work/export-2" rev-parse HEAD^)" = "$(git -C "$PUB" rev-parse master)"
yes_ "export --parent: one new commit" test "$(git -C "$work/export-2" rev-list --count HEAD^..HEAD)" = 1
git -C "$work/export-2" push -q "$PUB" master
check "export --parent pushes as a fast-forward" 0 $?
(cd "$P" && gitc fetch -q public && gitc merge -q -m "merge public master" public/master)
check "private master merges the public master again" 0 $?
(cd "$P" && bash scripts/check-public-parity.sh >"$work/p1.log" 2>&1)
check "parity after export --parent (default refs)" 0 $?

# --- 3. publish-branch.sh ----------------------------------------------------------------------------
gitc -C "$P" checkout -q -b claude/some-work
printf 'version 3\n' >"$P/app/main.txt"; printf 'new feature\n' >"$P/app/feature.txt"
printf 'private follow-up\n' >>"$P/.planning/STATE.md"
gitc -C "$P" add -A && gitc -C "$P" commit -q -m "feat(app): a feature"
(cd "$P" && bash scripts/publish-branch.sh claude/some-work >"$work/b0.log" 2>&1)
check "branch name outside the convention refused" 2 $?
(cd "$P" && bash scripts/publish-branch.sh --push feat/a-feature claude/some-work >"$work/b1.log" 2>&1)
check "publish-branch" 0 $?
yes_ "public branch exists" git -C "$PUB" rev-parse -q --verify refs/heads/feat/a-feature
yes_ "public branch is one commit above the public master" test "$(git -C "$PUB" rev-list --count master..feat/a-feature)" = 1
yes_ "public branch carries the change" test "$(git -C "$PUB" show feat/a-feature:app/main.txt)" = "version 3"
yes_ "public branch carries no private path" test -z "$(git -C "$PUB" ls-tree -r --name-only feat/a-feature | grep -E '^(\.planning|infra/pve02|CLAUDE\.md)')"

# An update of the private branch updates the open pull request: one more commit, fast-forward.
echo 'feature, reviewed' >"$P/app/feature.txt"; gitc -C "$P" commit -q -am "fix(app): review"
(cd "$P" && bash scripts/publish-branch.sh --push feat/a-feature claude/some-work >"$work/b1b.log" 2>&1)
check "publish-branch again on the open branch" 0 $?
yes_ "the branch grew by one commit" test "$(git -C "$PUB" rev-list --count master..feat/a-feature)" = 2

# --- 4. squash merge on the public side, merge back on the private side -------------------------------
gitc clone -q "$PUB" "$work/reviewer"
(cd "$work/reviewer" && gitc merge -q --squash origin/feat/a-feature && gitc commit -q -m "feat(app): a feature (#1)" && git push -q origin master)
check "pull request squash-merged on the public master" 0 $?
(cd "$P" && gitc checkout -q master && gitc merge -q -m "merge claude/some-work" claude/some-work && gitc fetch -q public &&
  gitc merge -m "merge public master" public/master >"$work/m.log" 2>&1)
check "git merge public/master without conflict" 0 $?
yes_ "private master keeps its private paths" test -f "$P/.planning/STATE.md"
(cd "$P" && bash scripts/check-public-parity.sh >"$work/p2.log" 2>&1)
check "parity after the merge back" 0 $?
yes_ "parity reports 0 gap" grep -q '^PARITY_OK' "$work/p2.log"

# --- 5. refusals --------------------------------------------------------------------------------------
before="$(git -C "$PUB" for-each-ref --format='%(refname) %(objectname)')"
gitc -C "$P" checkout -q -b claude/leak
printf 'see zz-private-host for details\n' >"$P/app/notes.txt"
gitc -C "$P" add -A && gitc -C "$P" commit -q -m "docs: notes"
(cd "$P" && bash scripts/publish-branch.sh --push docs/notes claude/leak >"$work/b2.log" 2>&1)
check "private pattern refuses the publication" 1 $?
yes_ "refusal names the private pattern" grep -q 'EXPORT_REFUSED: private patterns found' "$work/b2.log"
yes_ "public repository unchanged" test "$before" = "$(git -C "$PUB" for-each-ref --format='%(refname) %(objectname)')"
gitc -C "$P" checkout -q master
printf 'version 4, never published\n' >"$P/app/main.txt"; gitc -C "$P" commit -q -am "fix(app): unpublished"
(cd "$P" && bash scripts/check-public-parity.sh >"$work/p3.log" 2>&1)
check "unpublished change on the private master breaks the parity" 1 $?
yes_ "parity names the gap" grep -q '^PARITY_DIFF app/main.txt$' "$work/p3.log"
(cd "$P" && bash scripts/export-public.sh --parent "$PUB" --branch master "$work/export-3" HEAD~1 >"$work/e3.log" 2>&1)
check "nothing to publish is refused" 1 $?
yes_ "refusal says nothing to publish" grep -q 'nothing to publish' "$work/e3.log"

if [ "$failures" -eq 0 ]; then
  echo "PUBLISH_CYCLE_TEST PASS"
else
  echo "PUBLISH_CYCLE_TEST FAIL ($failures)"
  exit 1
fi
