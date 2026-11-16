#!/usr/bin/env bash
# Local proofs of infra/prod/archive-copy.sh (plan 04.2-02, task 2), on a throwaway tree.
#
#   bash infra/prod/tests/test-archive-copy.sh    # Git Bash on the workstation, or Linux bash
#
# No real archive, no ssh, no server: the source is a temporary tree of three files plus the
# sentinel; a remote destination goes through an ssh double that runs the command locally.
# Checked: copy, manifest (relative path, size, SHA-256 of every file, compared with an
# independent sha256sum), JSON receipt, --verify-only green; one corrupted byte, a deleted
# file, an extra file and an altered manifest named by --verify-only; a source changed during
# the copy (size, then mtime only) refused without receipt; missing source, missing or wrong
# sentinel, unmounted USB disk, source outside the USB disk, not enough room, non-empty
# destination without --resume, existing receipt, invalid edition refused; --dry-run writes
# nothing; the rsync transport never carries --delete.
# Prints ARCHIVE_COPY_SCRIPTS_OK only if every check passes; otherwise names the failures.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$(cd "$HERE/.." && pwd)/archive-copy.sh"
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAILURES=()
fail() { FAILURES+=("$1"); echo "FAIL: $1" >&2; }
pass() { echo "ok: $1"; }
hostpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
OUT="$WORK/out"

[ -n "$PY" ] || fail "python not found (needed for the JSON assertions)"

# expect <label> <exit> <text|-> <command...>: exit code and, unless -, a fixed string of the output.
expect() {
  local label="$1" want="$2" text="$3" rc
  shift 3
  "$@" >"$OUT" 2>&1
  rc=$?
  if [ "$rc" = "$want" ] && { [ "$text" = - ] || grep -qF -- "$text" "$OUT"; }; then
    pass "$label ($rc)"
  else
    fail "$label: exit $rc (want $want), text '${text}'"; sed 's/^/    /' "$OUT" >&2
  fi
}

# --- syntax -----------------------------------------------------------------------------------
for f in "$SCRIPT" "$HERE/test-archive-copy.sh"; do
  if bash -n "$f" 2>/dev/null; then pass "bash -n $(basename "$f")"; else fail "bash -n $(basename "$f")"; fi
done

# --- fixture ----------------------------------------------------------------------------------
SRC="$WORK/usb/datacommune-sources"
mk_source() {
  rm -rf "$SRC"; mkdir -p "$SRC/dvf/objects/ab" "$SRC/dvf/runs"
  printf 'datacommune-source-archive v1\n' > "$SRC/.datacommune-source-archive"
  printf 'mutation;valeur\n1;100000\n' > "$SRC/dvf/objects/ab/abcdef"
  head -c 4096 /dev/urandom > "$SRC/dvf/objects/ab/blob two.bin"
  printf '{"run_id": "48-20261019T171140Z"}\n' > "$SRC/dvf/runs/48-20261019T171140Z.json"
}
mk_source
export ARCHIVE_COPY_TRANSPORT=tar
run() { bash "$SCRIPT" --usb-mount none --edition archive-test "$@"; }

# --- copy and manifest -------------------------------------------------------------------------
DEST="$WORK/copy1"
expect "copy to a local destination" 0 "ARCHIVE_COPY_OK" run --source "$SRC" --dest "$DEST"
MAN="$DEST/.archive-copy/manifest-archive-test.sha256"
REC="$DEST/.archive-copy/receipt-archive-test.json"
# Independent manifest: same format, computed here from the source.
indep_manifest() {
  ( cd "$SRC" && find . -type f | LC_ALL=C sort | while IFS= read -r f; do
      f="${f#./}"; h="$(sha256sum -- "$f")"; printf '%s %s %s\n' "${h%% *}" "$(stat -c %s -- "$f")" "$f"
    done ) > "$WORK/expected.manifest"
}
indep_manifest
if [ -f "$MAN" ] && cmp -s "$MAN" "$WORK/expected.manifest"; then
  pass "manifest = relative path, size and SHA-256 of the 4 files"
else fail "manifest differs from the independent one"; diff "$WORK/expected.manifest" "$MAN" >&2; fi
if [ -n "$PY" ] && [ -f "$REC" ]; then
  if "$PY" - "$(hostpath "$REC")" "$(hostpath "$MAN")" <<'PY'
import hashlib, json, re, sys
rec = json.load(open(sys.argv[1], encoding="utf-8"))
man = open(sys.argv[2], "rb").read()
sizes = [int(l.split(" ", 2)[1]) for l in man.decode().splitlines()]
ok = (
    rec["edition"] == "archive-test" and rec["files"] == 4 and rec["bytes"] == sum(sizes)
    and rec["manifest"] == "manifest-archive-test.sha256"
    and rec["manifest_sha256"] == hashlib.sha256(man).hexdigest()
    and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", rec["date_utc"])
    and isinstance(rec["duration_s"], int) and rec["transport"] == "tar"
    and rec["source"].endswith("datacommune-sources") and rec["destination"].endswith("copy1")
)
sys.exit(0 if ok else 1)
PY
  then pass "receipt: date, source, destination, edition, files, bytes, manifest SHA-256, duration"
  else fail "receipt content"; cat "$REC" >&2; fi
else fail "receipt missing"; fi

expect "--verify-only on an intact copy" 0 "ARCHIVE_VERIFY_OK" run --dest "$DEST" --verify-only

# --- verify-only detects -----------------------------------------------------------------------
TARGET="$DEST/dvf/objects/ab/abcdef"
cp "$TARGET" "$WORK/saved"
printf 'X' | dd of="$TARGET" bs=1 seek=3 conv=notrunc 2>/dev/null
expect "one corrupted byte named" 81 "MISMATCH dvf/objects/ab/abcdef" run --dest "$DEST" --verify-only
cp "$WORK/saved" "$TARGET"
rm "$DEST/dvf/objects/ab/blob two.bin"
expect "deleted file named" 81 "MISSING dvf/objects/ab/blob two.bin" run --dest "$DEST" --verify-only
cp "$SRC/dvf/objects/ab/blob two.bin" "$DEST/dvf/objects/ab/blob two.bin"
printf 'stray\n' > "$DEST/dvf/stray.txt"
expect "extra file named" 81 "EXTRA dvf/stray.txt" run --dest "$DEST" --verify-only
rm "$DEST/dvf/stray.txt"
cp "$MAN" "$WORK/saved.man"; sed -i '1d' "$MAN"
expect "altered manifest refused against its receipt" 81 "MANIFEST_ALTERE" run --dest "$DEST" --verify-only
cp "$WORK/saved.man" "$MAN"
expect "copy green again after repair" 0 "ARCHIVE_VERIFY_OK" run --dest "$DEST" --verify-only

# --- unstable source: a tar double changes a source file during the transfer ---------------------
REAL_TAR="$(command -v tar)"
TAR_BIN="$WORK/tarbin"; mkdir -p "$TAR_BIN"
cat > "$TAR_BIN/tar" <<EOF
#!/usr/bin/env bash
# Only the reading side (-c) alters the source, before handing over to the real tar.
case " \$* " in *" -cf "*) eval "\$UNSTABLE_ACTION" ;; esac
exec "$REAL_TAR" "\$@"
EOF
chmod +x "$TAR_BIN/tar"
UNSTABLE_ACTION="printf 'more\n' >> '$SRC/dvf/objects/ab/abcdef'" PATH="$TAR_BIN:$PATH" \
  expect "source grown during the copy refused" 82 "UNSTABLE dvf/objects/ab/abcdef" run --source "$SRC" --dest "$WORK/copy2"
[ ! -e "$WORK/copy2/.archive-copy/receipt-archive-test.json" ] && pass "no receipt after an unstable copy" ||
  fail "receipt written after an unstable copy"
mk_source
UNSTABLE_ACTION="touch -d '2001-01-01 00:00:00' '$SRC/dvf/runs/48-20261019T171140Z.json'" PATH="$TAR_BIN:$PATH" \
  expect "source mtime changed during the copy refused" 82 "UNSTABLE dvf/runs/48-20261019T171140Z.json" run --source "$SRC" --dest "$WORK/copy3"
mk_source

# --- refusals before any write ------------------------------------------------------------------
expect "absent source" 80 "source_absente" run --source "$WORK/nowhere" --dest "$WORK/copy4"
rm "$SRC/.datacommune-source-archive"
expect "source without sentinel" 80 "sentinelle_absente" run --source "$SRC" --dest "$WORK/copy4"
printf 'something else\n' > "$SRC/.datacommune-source-archive"
expect "source with a wrong sentinel" 80 "sentinelle_invalide" run --source "$SRC" --dest "$WORK/copy4"
mk_source
expect "destination without the requested room" 80 "espace_insuffisant" run --source "$SRC" --dest "$WORK/copy4" --min-free-gb 100000000
expect "non-empty destination without --resume" 80 "destination_non_vide" run --source "$SRC" --dest "$DEST"
expect "existing receipt never overwritten" 80 "recu_existant" run --source "$SRC" --dest "$DEST" --resume
expect "invalid edition" 2 "--edition" bash "$SCRIPT" --usb-mount none --source "$SRC" --dest "$WORK/copy4" --edition 'a b'
[ ! -e "$WORK/copy4" ] && pass "refusals wrote nothing" || fail "a refusal created the destination"

# --- resume on a partial destination ----------------------------------------------------------
mkdir -p "$WORK/copy5/dvf/objects/ab"; head -c 10 "$SRC/dvf/objects/ab/abcdef" > "$WORK/copy5/dvf/objects/ab/abcdef"
expect "--resume completes a partial copy" 0 "ARCHIVE_COPY_OK" run --source "$SRC" --dest "$WORK/copy5" --resume

# --- USB disk mount ----------------------------------------------------------------------------
REAL_STAT="$(command -v stat)"
STAT_BIN="$WORK/statbin"; mkdir -p "$STAT_BIN"
cat > "$STAT_BIN/stat" <<EOF
#!/usr/bin/env bash
# Device numbers only: / is 801, any other path FAKE_USB_DEV; every other call is the real stat.
if [ "\$1" = -c ] && [ "\$2" = %D ]; then
  if [ "\$3" = / ]; then echo 801; else echo "\$FAKE_USB_DEV"; fi; exit 0
fi
exec "$REAL_STAT" "\$@"
EOF
chmod +x "$STAT_BIN/stat"
FAKE_USB_DEV=801 PATH="$STAT_BIN:$PATH" expect "USB disk not mounted (same device as /)" 80 "usb_non_monte" \
  bash "$SCRIPT" --usb-mount "$WORK/usb" --edition archive-test --source "$SRC" --dest "$WORK/copy6"
mkdir -p "$WORK/elsewhere"; cp -a "$SRC" "$WORK/elsewhere/"
FAKE_USB_DEV=812 PATH="$STAT_BIN:$PATH" expect "source outside the USB disk" 80 "source_hors_usb" \
  bash "$SCRIPT" --usb-mount "$WORK/usb" --edition archive-test --source "$WORK/elsewhere/datacommune-sources" --dest "$WORK/copy6"
FAKE_USB_DEV=812 PATH="$STAT_BIN:$PATH" expect "mounted USB disk accepted" 0 "ARCHIVE_COPY_OK" \
  bash "$SCRIPT" --usb-mount "$WORK/usb" --edition archive-test --source "$SRC" --dest "$WORK/copy6"

# --- dry run ------------------------------------------------------------------------------------
expect "--dry-run" 0 "DRY_RUN archive-copy" run --source "$SRC" --dest "$WORK/copy7" --dry-run
[ ! -e "$WORK/copy7" ] && pass "--dry-run wrote nothing" || fail "--dry-run created the destination"

# --- remote destination through an ssh double -----------------------------------------------------
SSH_BIN="$WORK/sshbin"; SSH_LOG="$WORK/ssh.log"; mkdir -p "$SSH_BIN"
cat > "$SSH_BIN/ssh" <<EOF
#!/usr/bin/env bash
# ssh [-o opt]... host command: logs the host, runs the command here (same stdin).
while [ "\$1" = -o ]; do shift 2; done
echo "ssh \$1" >> "$SSH_LOG"; shift
exec bash -c "\$*"
EOF
chmod +x "$SSH_BIN/ssh"
RDEST="$WORK/remote-copy"
PATH="$SSH_BIN:$PATH" expect "copy to user@host:dir" 0 "ARCHIVE_COPY_OK" run --source "$SRC" --dest "datacommune@prod.example:$RDEST"
PATH="$SSH_BIN:$PATH" expect "--verify-only on user@host:dir" 0 "ARCHIVE_VERIFY_OK" run --dest "datacommune@prod.example:$RDEST" --verify-only
indep_manifest; grep -q '^ssh datacommune@prod.example$' "$SSH_LOG" 2>/dev/null && cmp -s "$RDEST/.archive-copy/manifest-archive-test.sha256" "$WORK/expected.manifest" &&
  pass "remote copy went through ssh, same manifest" || fail "remote copy: ssh not used or manifest differs"

# --- rsync transport: resumable, never --delete -------------------------------------------------
RSYNC_BIN="$WORK/rsyncbin"; RSYNC_LOG="$WORK/rsync.log"; mkdir -p "$RSYNC_BIN"
cat > "$RSYNC_BIN/rsync" <<EOF
#!/usr/bin/env bash
# Logs its arguments, then copies like rsync -a would (local destination only).
echo "rsync \$*" >> "$RSYNC_LOG"
src="\${@: -2:1}"; dst="\${@: -1}"; mkdir -p "\$dst" && cp -a "\$src." "\$dst"
EOF
chmod +x "$RSYNC_BIN/rsync"
ARCHIVE_COPY_TRANSPORT=auto PATH="$RSYNC_BIN:$PATH" expect "rsync transport when present" 0 "ARCHIVE_COPY_OK" run --source "$SRC" --dest "$WORK/copy8"
if grep -q -- '-a --partial --inplace' "$RSYNC_LOG" 2>/dev/null && ! grep -q -- '--delete' "$RSYNC_LOG"; then
  pass "rsync -a --partial --inplace, without --delete"
else fail "rsync arguments: $(cat "$RSYNC_LOG" 2>/dev/null)"; fi

# --- result -------------------------------------------------------------------------------------
if [ "${#FAILURES[@]}" -eq 0 ]; then echo ARCHIVE_COPY_SCRIPTS_OK; exit 0; fi
echo "ARCHIVE_COPY_SCRIPTS_BAD (${#FAILURES[@]} failures)" >&2
printf '  - %s\n' "${FAILURES[@]}" >&2
exit 1
