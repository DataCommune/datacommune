#!/usr/bin/env bash
# Local proofs of infra/prod/import.sh (plan 04.2-03, task 1), without server, database or import.
#
#   bash infra/prod/tests/test-prod-import.sh    # Git Bash on the workstation, or Linux bash
#
# import.sh runs against a temporary PROD_DIR (current_sha, release, imports) and doubles of
# docker and df that log every call: run_etl succeeds or fails on demand, etl_runs rows live
# in a text file, the receipt exporter writes a minimal receipt and its pointer.
# Checked: --dry-run prints the 21 datasets in the exact order, no --from-archive, and calls
# nothing; an unknown dataset, a malformed run id and --replay on the 17 pipelines without
# archive_replay are refused before any docker call; --replay dvf adds --from-archive; a full
# sequence runs the 21 datasets in order and leaves a receipt and measurements for each; a
# failure stops the sequence at that dataset; --resume skips datasets whose receipt is
# success (a hand-made receipt is accepted) and refuses one without it; a release whose
# commit is missing from the server repository is refused before any run_etl.
# Prints PROD_IMPORT_SCRIPTS_OK only if every check passes; otherwise names the failures.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$(cd "$HERE/.." && pwd)/import.sh"
ORDER="monuments loyers transports scolaire zonage_abc fiscalite revenus demographie vacance copropriete permis antennes equipements ban dpe batiments foncier_pm cadastre connectivite rnb_liens dvf"
REPLAY_OK="vacance dpe batiments dvf"
SHA=0123456789abcdef0123456789abcdef01234567

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAILURES=()
fail() { FAILURES+=("$1"); echo "FAIL: $1" >&2; }
pass() { echo "ok: $1"; }
OUT="$WORK/out"; CALLS="$WORK/calls.log"; ROWS="$WORK/etl_runs"

# --- server tree and doubles ----------------------------------------------------------------------
export PROD_DIR="$WORK/opt"
reset_server() {
  rm -rf "$PROD_DIR"; mkdir -p "$PROD_DIR/releases/${SHA:0:12}/api" "$PROD_DIR/imports" "$PROD_DIR/repo.git"
  echo "$SHA" > "$PROD_DIR/current_sha"
  : > "$CALLS"; : > "$ROWS"
}
FAKE_BIN="$WORK/bin"; mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/docker" <<EOF
#!/usr/bin/env bash
# docker double: logs the call; answers run_etl, the SQL of import.sh, du, memory.peak, the exporter.
echo "docker \$*" >> "$CALLS"
args=" \$* "
case "\$args" in
  *" python -m run_etl "*)
    ds="\$(printf '%s\n' "\$@" | grep -A1 -x run_etl | tail -1)"
    id=\$(( \$(wc -l < "$ROWS") + 1 ))
    if [ "\$ds" = "\${FAKE_FAIL:-}" ]; then echo "\$id \$ds failed" >> "$ROWS"; echo "boom" >&2; exit 1; fi
    # Long enough for the background sampler of import.sh to take a sample.
    sleep 0.5; echo "\$id \$ds success" >> "$ROWS"; echo "run_etl \$ds done"; exit 0 ;;
  *" scripts.export_import_manifest "*)
    ds="\$(printf '%s\n' "\$@" | grep -A1 -x -- --dataset | tail -1)"
    rid="\$(printf '%s\n' "\$@" | grep -A1 -x -- --run-id | tail -1)"
    st="\$(awk -v i="\${rid%%-*}" '\$1 == i { print \$3 }' "$ROWS")"
    mkdir -p "$PROD_DIR/imports/\$ds"
    printf '{"dataset": "%s", "etl_run": {"status": "%s"}, "run_id": "%s"}\n' "\$ds" "\$st" "\$rid" > "$PROD_DIR/imports/\$ds/\$rid.json"
    printf '{"dataset": "%s", "manifest": "%s.json", "run_id": "%s"}\n' "\$ds" "\$rid" "\$rid" > "$PROD_DIR/imports/\$ds/latest.json"
    echo "MANIFEST \$rid.json"; exit 0 ;;
  *" cat-file -e "*) [ -z "\${FAKE_NO_REPO:-}" ]; exit ;;
  *" du -sb "*) printf '16777216\t/var/lib/postgresql/data/pg_wal\n'; exit 0 ;;
  *"/memory.peak "*) echo 2147483648; exit 0 ;;
  *" image inspect "*|*" build "*) exit 0 ;;
  *" ps "*) exit 0 ;;
  *" exec -T db "*)
    sql="\$(cat)"
    case "\$sql" in
      *"max(id)"*) awk 'BEGIN { m = 0 } \$1 > m { m = \$1 } END { print m }' "$ROWS" ;;
      *"SELECT id FROM etl_runs"*)
        ds="\$(printf '%s' "\$sql" | sed -n "s/.*dataset_name = '\([a-z_]*\)'.*/\1/p")"
        gt="\$(printf '%s' "\$sql" | sed -n 's/.*id > \([0-9]*\).*/\1/p')"
        awk -v d="\$ds" -v g="\$gt" '\$2 == d && \$1 > g { i = \$1 } END { if (i) print i }' "$ROWS" ;;
      *"to_char(started_at"*)
        id="\$(printf '%s' "\$sql" | sed -n 's/.*WHERE id = \([0-9]*\).*/\1/p')"
        awk -v i="\$id" '\$1 == i { printf "%s-20261024T120000Z %s 12.5\n", \$1, \$3 }' "$ROWS" ;;
    esac
    exit 0 ;;
esac
exit 0
EOF
cat > "$FAKE_BIN/df" <<EOF
#!/usr/bin/env bash
echo "df \$*" >> "$CALLS"
printf '      Used\n%s\n' 400000000000
EOF
# python3 as on the server: the first interpreter that really runs (python3 can be a Windows
# Store stub), absolute paths converted for a Windows python under Git Bash.
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    PY="$(command -v "$c")"; break
  fi
done
[ -n "$PY" ] || fail "python not found (import.sh reads receipts with python3)"
cat > "$FAKE_BIN/python3" <<EOF
#!/usr/bin/env bash
args=()
for a in "\$@"; do
  case "\$a" in /*) command -v cygpath >/dev/null 2>&1 && a="\$(cygpath -m "\$a")" ;; esac
  args+=("\$a")
done
exec "$PY" "\${args[@]}"
EOF
chmod +x "$FAKE_BIN/docker" "$FAKE_BIN/df" "$FAKE_BIN/python3"
run() { PATH="$FAKE_BIN:$PATH" IMPORT_SAMPLE_INTERVAL=1 bash "$SCRIPT" "$@"; }

# expect <label> <exit> <text|-> <args...>
expect() {
  local label="$1" want="$2" text="$3" rc
  shift 3
  run "$@" >"$OUT" 2>&1
  rc=$?
  if [ "$rc" = "$want" ] && { [ "$text" = - ] || grep -qF -- "$text" "$OUT"; }; then pass "$label ($rc)"
  else fail "$label: exit $rc (want $want), text '$text'"; sed 's/^/    /' "$OUT" >&2; fi
}
etl_calls() { grep ' python -m run_etl ' "$CALLS" | sed 's/.* python -m run_etl \([a-z_]*\).*/\1/' | tr '\n' ' ' | sed 's/ $//'; }

# --- syntax ------------------------------------------------------------------------------------
for f in "$SCRIPT" "$HERE/test-prod-import.sh"; do
  if bash -n "$f" 2>/dev/null; then pass "bash -n $(basename "$f")"; else fail "bash -n $(basename "$f")"; fi
done

# --- dry run: exact order, no --from-archive, nothing called --------------------------------------
reset_server
expect "--dry-run" 0 "DRY_RUN import" --dry-run
got="$(sed -n 's/.* python -m run_etl \([a-z_]*\).*/\1/p' "$OUT" | tr '\n' ' ' | sed 's/ $//')"
[ "$got" = "$ORDER" ] && pass "--dry-run prints the 21 datasets in order" || fail "--dry-run order: '$got'"
grep -q -- '--from-archive' "$OUT" && fail "--dry-run carries --from-archive" || pass "no --from-archive by default"
[ -s "$CALLS" ] && fail "--dry-run called: $(tr '\n' ';' < "$CALLS")" || pass "--dry-run calls neither docker nor df"
rm -rf "$PROD_DIR"
expect "--dry-run without server tree (workstation)" 0 "DRY_RUN import" --dry-run

# --- refusals before any call ---------------------------------------------------------------------
reset_server
expect "unknown dataset in --resume" 2 "jeu inconnu: cadastres" --resume cadastres
expect "unknown dataset in --replay" 2 "jeu inconnu: rnb" --replay rnb 1-20261024T120000Z
expect "malformed run id" 2 "run_id inconnu" --replay dvf 48-latest
refused=0
for ds in $ORDER; do
  case " $REPLAY_OK " in *" $ds "*) continue ;; esac
  run --replay "$ds" 1-20261024T120000Z >"$OUT" 2>&1
  rc=$?
  if [ "$rc" = 80 ] && grep -qF "archive_replay_non_supporte: $ds" "$OUT"; then refused=$((refused + 1)); else fail "--replay $ds not refused (exit $rc)"; fi
done
[ "$refused" = 17 ] && pass "--replay refused for the 17 pipelines without archive_replay"
[ -s "$CALLS" ] && fail "a refusal called: $(tr '\n' ';' < "$CALLS")" || pass "refusals call nothing"
expect "--replay dvf --dry-run" 0 "run_etl dvf --from-archive 48-20261019T171140Z" --replay dvf 48-20261019T171140Z --dry-run
[ "$(grep -c ' python -m run_etl ' "$OUT")" = 1 ] && pass "--replay runs one dataset only" || fail "--replay ran more than one dataset"

# --- full sequence -------------------------------------------------------------------------------
reset_server
expect "full sequence" 0 "IMPORT_SEQUENCE_OK"
[ "$(etl_calls)" = "$ORDER" ] && pass "run_etl called for the 21 datasets in order" || fail "run order: '$(etl_calls)'"
n=0
for ds in $ORDER; do
  [ -f "$PROD_DIR/imports/$ds/1-20261024T120000Z.json" ] || [ -n "$(ls "$PROD_DIR/imports/$ds/"*-20261024T120000Z.json 2>/dev/null)" ] || continue
  m="$(ls "$PROD_DIR/imports/$ds/"*.mesures.json 2>/dev/null | head -1)"
  [ -n "$m" ] && grep -q '"memory_peak_bytes": 2147483648' "$m" && grep -q '"wal_dir_peak_bytes": 16777216' "$m" &&
    grep -q '"disk_peak_bytes": 400000000000' "$m" && n=$((n + 1))
done
[ "$n" = 21 ] && pass "a receipt and its measurements (disk, WAL, memory) for each of the 21" || fail "receipts with measurements: $n/21"
grep -q -- '--from-archive' "$CALLS" && fail "the sequence used --from-archive" || pass "the first import downloads (no --from-archive)"
grep -q 'export_import_manifest.* --repo-dir /repo' "$CALLS" && pass "receipt exported against the server repository" || fail "exporter without --repo-dir /repo"

# --- failure stops the sequence ---------------------------------------------------------------------
reset_server
FAKE_FAIL=scolaire expect "failure of scolaire" 1 "IMPORT scolaire failed"
[ "$(etl_calls)" = "monuments loyers transports scolaire" ] && pass "sequence stopped at the failed dataset" || fail "after failure: '$(etl_calls)'"
grep -q 'IMPORT_SEQUENCE_OK' "$OUT" && fail "IMPORT_SEQUENCE_OK after a failure" || pass "no IMPORT_SEQUENCE_OK after a failure"

# --- resume ------------------------------------------------------------------------------------------
: > "$CALLS"
expect "--resume scolaire" 0 "IMPORT_SEQUENCE_OK" --resume scolaire
want="${ORDER#monuments loyers transports }"
[ "$(etl_calls)" = "$want" ] && pass "--resume skips the three success receipts, runs scolaire..dvf" || fail "resume order: '$(etl_calls)'"
reset_server
# Hand-made receipts: monuments success, loyers failed.
mkdir -p "$PROD_DIR/imports/monuments" "$PROD_DIR/imports/loyers"
printf '{"dataset": "monuments", "etl_run": {"status": "success"}, "run_id": "7-20261020T100000Z"}\n' > "$PROD_DIR/imports/monuments/7-20261020T100000Z.json"
printf '{"dataset": "monuments", "manifest": "7-20261020T100000Z.json", "run_id": "7-20261020T100000Z"}\n' > "$PROD_DIR/imports/monuments/latest.json"
printf '{"dataset": "loyers", "etl_run": {"status": "failed"}, "run_id": "8-20261020T110000Z"}\n' > "$PROD_DIR/imports/loyers/8-20261020T110000Z.json"
printf '{"dataset": "loyers", "manifest": "8-20261020T110000Z.json", "run_id": "8-20261020T110000Z"}\n' > "$PROD_DIR/imports/loyers/latest.json"
expect "--resume loyers on a hand-made success receipt" 0 "IMPORT_SEQUENCE_OK" --resume loyers
[ "$(etl_calls)" = "${ORDER#monuments }" ] && pass "hand-made success receipt accepted" || fail "resume loyers: '$(etl_calls)'"
reset_server
mkdir -p "$PROD_DIR/imports/monuments" "$PROD_DIR/imports/loyers"
printf '{"dataset": "monuments", "etl_run": {"status": "success"}, "run_id": "7-20261020T100000Z"}\n' > "$PROD_DIR/imports/monuments/7-20261020T100000Z.json"
printf '{"dataset": "monuments", "manifest": "7-20261020T100000Z.json", "run_id": "7-20261020T100000Z"}\n' > "$PROD_DIR/imports/monuments/latest.json"
printf '{"dataset": "loyers", "etl_run": {"status": "failed"}, "run_id": "8-20261020T110000Z"}\n' > "$PROD_DIR/imports/loyers/8-20261020T110000Z.json"
printf '{"dataset": "loyers", "manifest": "8-20261020T110000Z.json", "run_id": "8-20261020T110000Z"}\n' > "$PROD_DIR/imports/loyers/latest.json"
expect "--resume past a failed receipt refused" 1 "reçu success absent: loyers" --resume transports
expect "--resume past a missing receipt refused" 1 "reçu success absent: loyers" --resume scolaire
[ -z "$(etl_calls)" ] && pass "a refused resume runs nothing" || fail "refused resume ran: '$(etl_calls)'"

# --- release commit absent from the server repository -------------------------------------------------
reset_server
FAKE_NO_REPO=1 expect "commit missing from repo.git" 80 "repo_sha absent"
[ -z "$(etl_calls)" ] && pass "no run_etl without a repository for the receipts" || fail "ran without repository: '$(etl_calls)'"

# --- --setup-repo: the bare repository of the receipts, from a git bundle ------------------------------
reset_server
rm -rf "$PROD_DIR/repo.git"
expect "--setup-repo without bundle" 80 "paquet git absent" --setup-repo "$PROD_DIR/datacommune.bundle"
: > "$PROD_DIR/datacommune.bundle"; mkdir "$PROD_DIR/repo.git"
expect "--setup-repo while repo.git exists" 80 "existe déjà" --setup-repo "$PROD_DIR/datacommune.bundle"
[ -s "$CALLS" ] && fail "a --setup-repo refusal called docker" || pass "--setup-repo refusals call nothing"
rmdir "$PROD_DIR/repo.git"
expect "--setup-repo --dry-run" 0 "DRY_RUN import setup-repo" --setup-repo "$PROD_DIR/datacommune.bundle" --dry-run
[ -s "$CALLS" ] && fail "--setup-repo --dry-run called docker" || pass "--setup-repo --dry-run calls nothing"
expect "--setup-repo" 0 "REPO_OK" --setup-repo "$PROD_DIR/datacommune.bundle"
grep -q 'git clone -q --bare /bundle/datacommune.bundle /w/repo.git' "$CALLS" && [ -z "$(etl_calls)" ] &&
  pass "bare clone of the bundle in the tool image, no import" || fail "--setup-repo calls: $(tr '\n' ';' < "$CALLS")"

# --- result -------------------------------------------------------------------------------------------
if [ "${#FAILURES[@]}" -eq 0 ]; then echo PROD_IMPORT_SCRIPTS_OK; exit 0; fi
echo "PROD_IMPORT_SCRIPTS_BAD (${#FAILURES[@]} failures)" >&2
printf '  - %s\n' "${FAILURES[@]}" >&2
exit 1
