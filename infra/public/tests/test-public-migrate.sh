#!/usr/bin/env bash
# Prove infra/public/public-migrate.sh (bascule of 05-05) without a CT, without docker and
# without ssh. Fake ssh decodes the side script and runs it in a local bash whose "side" is
# the CT id (pct:<id>) or the ssh alias; fake docker answers from $SIM/<side>/ (volumes,
# running services, alembic revision); python3 is the real interpreter (sqlite3).
# Cases:
#   (a) refusals before any remote call (exit 2, no ssh): same source and target, target =
#       the serving public CT, the work base, a protected CT, --apply outside goatcounter
#   (b) survey: facts of both sides, MIGRATE_SURVEY ok
#   (c) goatcounter without --apply: DRY_RUN, nothing written on the target
#   (d) --apply while goatcounter runs on the target: 80, target untouched
#   (e) --apply nominal: volume created on the target, copy passes integrity_check, same
#       fingerprint as the source, stale -wal/-shm of the target removed
#   (f) replay: MIGRATE_GOATCOUNTER unchanged, nothing written (idempotent)
#   (g) compare: ok when both sides agree; DIFF and exit 3 on another alembic revision
#   (h) the source never receives a script that writes; its db and -wal are byte-identical
# Prints PUBLIC_MIGRATE_OK only if every case passes.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${PUBLIC_MIGRATE_SCRIPT:-$HERE/../public-migrate.sh}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export SIM="$WORK/sim" FAKE_LOG="$WORK/calls.log"
FAKE_BIN="$WORK/bin"
mkdir -p "$FAKE_BIN" "$SIM"

FAILS=0
fail() { echo "FAIL $*"; FAILS=$((FAILS + 1)); }
pass() { echo "PASS $*"; }

PY="$(command -v python3 2>/dev/null || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sqlite3' >/dev/null 2>&1; then PY="$(command -v python)"; fi
export REAL_PY="$PY"
to_native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

# --- doubles -------------------------------------------------------------------------
cat >"$FAKE_BIN/ssh" <<'EOF'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host="$1" cmd="$2"
case "$cmd" in
  "pct exec "*) side="ct$(printf '%s' "$cmd" | awk '{print $3}')"; cmd="${cmd#pct exec * -- }" ;;
  *) side="$host" ;;
esac
b64="$(printf '%s' "$cmd" | sed -n "s/.*printf %s \([A-Za-z0-9+\/=]*\) | base64 -d.*/\1/p")"
printf '%s\n' "$b64" | base64 -d > "$SIM/last-script-$side"
{ echo "=== $side"; cat "$SIM/last-script-$side"; } | tee -a "$SIM/all.log" >> "$FAKE_LOG"
SIDE="$side" exec bash -c "$cmd"
EOF
cat >"$FAKE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
root="$SIM/$SIDE"
case "$1 $2" in
  "volume inspect") v="${*: -1}"; [ -d "$root/volumes/$v" ] || exit 1; printf '%s\n' "$root/volumes/$v" ;;
  "volume create") v="${*: -1}"; mkdir -p "$root/volumes/$v"; echo "WRITE $SIDE volume create $v" >> "$FAKE_LOG" ;;
  "ps -q")
    svc="$(printf '%s\n' "$@" | sed -n 's/^label=com.docker.compose.service=//p')"
    [ -f "$root/running/$svc" ] && echo "id-$svc" ; exit 0 ;;
  "ps --filter") ls "$root/running" 2>/dev/null ;;
  "exec id-db") cat "$root/alembic" ;;
  *) echo "unexpected docker $*" >&2; exit 9 ;;
esac
EOF
cat >"$FAKE_BIN/python3" <<'EOF'
#!/usr/bin/env bash
args=()
for a in "$@"; do
  if [ "${a#/}" != "$a" ] && [ -e "$(dirname "$a")" ] && command -v cygpath >/dev/null 2>&1; then a="$(cygpath -m "$a")"; fi
  args+=("$a")
done
exec "$REAL_PY" "${args[@]}"
EOF
cat >"$FAKE_BIN/curl" <<'EOF'
#!/usr/bin/env bash
cat "$SIM/$SIDE/health" 2>/dev/null || exit 7
EOF
for t in chown chmod; do printf '#!/usr/bin/env bash\nexit 0\n' > "$FAKE_BIN/$t"; done
chmod +x "$FAKE_BIN"/*
export PATH="$FAKE_BIN:$PATH"

# --- sides: source ct903 (serving), target ct904 (rehearsal) -------------------------
VOL=datacommune-public_goatcounter_data
mk_side() { # mk_side <side> <alembic>
  mkdir -p "$SIM/$1/running"; printf '%s\n' "$2" > "$SIM/$1/alembic"
  printf '{"statut": "ok"}' > "$SIM/$1/health"; touch "$SIM/$1/running/db"
}
mk_side ct903 116_head; mk_side ct904 116_head
touch "$SIM/ct903/running/goatcounter"
mkdir -p "$SIM/ct903/volumes/$VOL"
"$REAL_PY" - "$(to_native "$SIM/ct903/volumes/$VOL/db.sqlite3")" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.execute("PRAGMA journal_mode=WAL")
c.execute("CREATE TABLE hit_counts (site_id INT, path_id INT, hour TEXT, total INT)")
c.executemany("INSERT INTO hit_counts VALUES (1, ?, '2026-11-12 10:00:00', 1)", [(i,) for i in range(500)])
c.commit(); c.close()
PY
# db and -wal hold the data; -shm is the wal-index every reader updates (not data). A
# read-only open creates an EMPTY -wal when none exists (SQLite); on the public container it exists, held
# by GoatCounter. Empty files are therefore not compared.
src_sum() { (cd "$SIM/ct903/volumes/$VOL" && find . -maxdepth 1 \( -name db.sqlite3 -o -name db.sqlite3-wal \) -size +0 -exec sha256sum {} + | sort); }
SRC_BEFORE="$(src_sum)"

export PUBLIC_SETTINGS_FILE=/nonexistent PUBLIC_SSH_HOST=hv PUBLIC_PROJECT=datacommune-public
export PUBLIC_DIR=/opt/datacommune-public PUBLIC_SOURCE_CTID=902 PUBLIC_PROTECTED_CTIDS=975 PUBLIC_CTID=903
run() { OUT="$(bash "$SCRIPT" "$@" 2>&1)"; RC=$?; }
calls() { local n; n="$(grep -c '^=== ' "$FAKE_LOG" 2>/dev/null)"; echo "${n:-0}"; }

# (a) refusals before any remote call
: > "$FAKE_LOG"
for args in "survey --source pct:903 --target pct:903" "goatcounter --source pct:904 --target pct:903 --apply" \
            "survey --source pct:903 --target pct:902" "survey --source pct:903 --target pct:975" \
            "survey --source pct:903 --target pct:904 --apply" "survey --source pct:903 --target bogus"; do
  # shellcheck disable=SC2086
  run $args
  if [ "$RC" = 2 ] && [ "$(calls)" = 0 ]; then pass "(a) refused: $args"; else fail "(a) $args -> rc=$RC calls=$(calls): $OUT"; fi
done

# (b) survey
: > "$FAKE_LOG"; run survey --source pct:903 --target pct:904
if [ "$RC" = 0 ] && grep -q '^MIGRATE_SURVEY ok' <<<"$OUT" && grep -q '^source goatcounter sha256=[0-9a-f]\{64\} .*views=500' <<<"$OUT" \
   && grep -q '^target goatcounter sha256=none' <<<"$OUT" && grep -q '^source alembic=116_head' <<<"$OUT"; then
  pass "(b) survey"; else fail "(b) survey rc=$RC: $OUT"; fi

# (c) dry run
: > "$FAKE_LOG"; run goatcounter --source pct:903 --target pct:904
if [ "$RC" = 0 ] && grep -q 'MIGRATE_GOATCOUNTER DRY_RUN' <<<"$OUT" && [ ! -e "$SIM/ct904/volumes" ] && ! grep -q '^WRITE' "$FAKE_LOG"; then
  pass "(c) dry run writes nothing"; else fail "(c) rc=$RC: $OUT"; fi

# (d) goatcounter running on the target
touch "$SIM/ct904/running/goatcounter"; : > "$FAKE_LOG"
run goatcounter --source pct:903 --target pct:904 --apply
if [ "$RC" = 80 ] && grep -q 'MIGRATE_GOATCOUNTER REFUSED target' <<<"$OUT" && [ ! -e "$SIM/ct904/volumes/$VOL/db.sqlite3" ]; then
  pass "(d) refused while goatcounter runs on the target"; else fail "(d) rc=$RC: $OUT"; fi
rm -f "$SIM/ct904/running/goatcounter"

# (e) nominal copy; stale WAL files of the target must go
rm -rf "$SIM/ct904/volumes"
: > "$FAKE_LOG"; run goatcounter --source pct:903 --target pct:904 --apply
tdb="$SIM/ct904/volumes/$VOL/db.sqlite3"
if [ "$RC" = 0 ] && grep -q '^MIGRATE_GOATCOUNTER copied sha256=' <<<"$OUT" && ! grep -q 'source moved' <<<"$OUT" \
   && [ -f "$tdb" ] && [ ! -e "$tdb.part" ] && grep -q '^WRITE ct904 volume create' "$FAKE_LOG" \
   && [ "$("$REAL_PY" -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("select sum(total) from hit_counts").fetchone()[0])' "$(to_native "$tdb")")" = 500 ]; then
  pass "(e) copy lands, 500 hits, same fingerprint"; else fail "(e) rc=$RC: $OUT"; fi
# a later re-copy over a target that has stale -wal/-shm
"$REAL_PY" -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("insert into hit_counts values (1, 999, \"2026-11-12 11:00:00\", 1)"); c.commit()' "$(to_native "$SIM/ct903/volumes/$VOL/db.sqlite3")"
SRC_BEFORE="$(src_sum)"
printf 'junk' > "$tdb-wal"; printf 'junk' > "$tdb-shm"
run goatcounter --source pct:903 --target pct:904 --apply
if [ "$RC" = 0 ] && [ "$(cat "$tdb-wal" 2>/dev/null)" != junk ] && [ "$(cat "$tdb-shm" 2>/dev/null)" != junk ] && grep -q 'views=501' <<<"$OUT"; then
  pass "(e) re-copy removes the stale -wal/-shm of the target"; else fail "(e) re-copy rc=$RC: $OUT"; fi

# (f) idempotent replay
: > "$FAKE_LOG"; before="$(sha256sum "$tdb")"
run goatcounter --source pct:903 --target pct:904 --apply
if [ "$RC" = 0 ] && grep -q '^MIGRATE_GOATCOUNTER unchanged' <<<"$OUT" && [ "$before" = "$(sha256sum "$tdb")" ] && ! grep -q '^WRITE' "$FAKE_LOG"; then
  pass "(f) replay is a no-op"; else fail "(f) rc=$RC: $OUT"; fi

# (g) compare
run compare --source pct:903 --target pct:904
if [ "$RC" = 0 ] && grep -q '^MIGRATE_COMPARE ok' <<<"$OUT"; then pass "(g) compare ok"; else fail "(g) rc=$RC: $OUT"; fi
printf '115_older\n' > "$SIM/ct904/alembic"
run compare --source pct:903 --target pct:904
if [ "$RC" = 3 ] && grep -q '^DIFF alembic source=116_head target=115_older' <<<"$OUT"; then pass "(g) compare DIFF"; else fail "(g) DIFF rc=$RC: $OUT"; fi

# (h) the source only read
if [ "$(src_sum)" = "$SRC_BEFORE" ]; then pass "(h) source files byte-identical"; else fail "(h) source files changed"; fi
if awk '/^=== /{side=$2; next} side=="ct903" && /mv -f|cat > "\$part"|volume create/' "$FAKE_LOG" | grep -q .; then
  fail "(h) a writing script reached the source"; else pass "(h) no writing script reached the source"; fi

[ "$FAILS" = 0 ] && { echo PUBLIC_MIGRATE_OK; exit 0; }
echo "PUBLIC_MIGRATE_FAILED $FAILS"; exit 1
