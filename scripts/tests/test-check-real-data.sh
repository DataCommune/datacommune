#!/usr/bin/env bash
# Test of scripts/check-real-data.py: the export guard that refuses, in a tree, an address, an
# identifier or a sale of the real data set the fixtures were captured from (05-07, lot 3).
#
# Every value here is invented (street « Rue du Contrôle », key 99999_zz99, prices 123 457 €): the
# real list is private and never enters the public tree. Needs python. No network, no Docker.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/../check-real-data.py"
python_bin="${PYTHON:-python}"

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

run() { # <list> <tree> <log>: the guard's exit code
  "$python_bin" "$guard" "$1" "$2" >"$3" 2>&1
}

cat >"$work/list.json" <<'EOF'
{
 "chaines": ["99999_zz99_", "7 Rue du Contrôle", "9999000ZZ0001"],
 "ventes": [{"date": "2024-03-14", "prix": 123457}, {"date": "2022-11-02", "prix": 250000}]
}
EOF
tree="$work/tree"
mkdir -p "$tree/a/b" "$tree/.git"
printf 'nothing real here, 2024-03-14 is only a date\n' >"$tree/a/clean.txt"
printf '{"prix": 250000, "date": "2023-01-01"}\n' >"$tree/a/b/other-sale.json"
printf 'leaked key 99999_zz99_00001 in a git object\n' >"$tree/.git/ignored"
printf '\x00\x01binary 7 Rue du Contrôle\x00' >"$tree/a/blob.bin"

# 1. A clean tree passes; a date or a price alone is not a sale; .git and binaries are skipped.
run "$work/list.json" "$tree" "$work/clean.log"
check "clean tree passes" 0 $?
grep -q '^REAL_DATA_CHECK .*found=0' "$work/clean.log"
check "clean tree reports its counts" 0 $?

# 2. An address key, case changed, is refused; the value itself is never printed.
printf 'export const ID = "99999_ZZ99_00004";\n' >"$tree/a/key.ts"
run "$work/list.json" "$tree" "$work/key.log"
check "address key is refused" 1 $?
grep -q '^REAL_DATA_FOUND a/key.ts chaine#0$' "$work/key.log"
check "refusal names the file and the entry, not the value" 0 $?
! grep -q -i 'zz99' "$work/key.log"
check "refusal does not print the value" 0 $?
rm "$tree/a/key.ts"

# 3. A label in a comment is refused.
printf '// opened on 7 rue du contrôle, Nantes\n' >"$tree/a/spec.ts"
run "$work/list.json" "$tree" "$work/label.log"
check "address label is refused" 1 $?
rm "$tree/a/spec.ts"

# 4. A sale: its date and its price in the same file, raw or as rendered (narrow no-break space).
printf '{"date": "2024-03-14", "prix": 123457}\n' >"$tree/a/raw.json"
run "$work/list.json" "$tree" "$work/raw.log"
check "raw sale is refused" 1 $?
grep -q '^REAL_DATA_FOUND a/raw.json vente#0$' "$work/raw.log"
check "raw sale is named" 0 $?
rm "$tree/a/raw.json"
printf 'Date: 14/03/2024, Prix: 123\xe2\x80\xaf457\xe2\x80\xaf\xe2\x82\xac\n' >"$tree/a/rendered.txt"
run "$work/list.json" "$tree" "$work/rendered.log"
check "rendered sale is refused" 1 $?
rm "$tree/a/rendered.txt"

printf "['mars 2024 · Vente', '123 457 €']\n" >"$tree/a/month.spec.ts"
run "$work/list.json" "$tree" "$work/month.log"
check "sale rendered with its month is refused" 1 $?
rm "$tree/a/month.spec.ts"

# A date and a price far apart in a file are not one sale (population 250 000, report of 2022).
{ echo 'population: 250 000'; printf '%2000s' '' | tr ' ' x; echo; echo 'novembre 2022'; } >"$tree/a/far.ts"
run "$work/list.json" "$tree" "$work/far.log"
check "a date far from the price is not a sale" 0 $?
rm "$tree/a/far.ts"

# 5. A longer number that contains the price is not the price.
printf '{"date": "2024-03-14", "id": 91234570}\n' >"$tree/a/longer.json"
run "$work/list.json" "$tree" "$work/longer.log"
check "a number containing the price is not a sale" 0 $?
rm "$tree/a/longer.json"

# 6. A blind check is refused: missing list, empty list, missing tree.
run "$work/missing.json" "$tree" "$work/missing.log"
check "missing list is refused" 2 $?
printf '{"chaines": [], "ventes": []}\n' >"$work/empty.json"
run "$work/empty.json" "$tree" "$work/empty.log"
check "empty list is refused" 2 $?
grep -q 'REAL_DATA_BLIND' "$work/empty.log"
check "empty list names the refusal" 0 $?
run "$work/list.json" "$work/absent" "$work/absent.log"
check "missing tree is refused" 2 $?

if [ "$failures" -eq 0 ]; then
  echo "REAL_DATA_GUARD_TEST PASS"
else
  echo "REAL_DATA_GUARD_TEST FAIL ($failures)"
  exit 1
fi
