#!/usr/bin/env bash
# Second copy of the source archive, checked file by file (plan 04.2-02, D-126).
#
#   archive-copy.sh --source <dir> --dest <user@host:dir | dir> --edition <label>
#                   [--min-free-gb <n>] [--usb-mount <dir|none>] [--dry-run] [--resume]
#   archive-copy.sh --dest <user@host:dir | dir> --edition <label> --verify-only
#
# Run where the source is (the hypervisor, as root: the archive belongs to 101000), e.g.
#   scp infra/prod/archive-copy.sh <host>:/tmp/ && ssh <host> 'bash /tmp/archive-copy.sh ...'
# A remote destination is reached by `ssh -o BatchMode=yes <user@host>` (key of the machine
# that runs the script). The source is only read: no --delete, nothing written under it.
#
# Copy:
#   1. refuses (80) an absent source, a source without the sentinel .datacommune-source-archive
#      (content `datacommune-source-archive v1`), an unmounted USB disk (--usb-mount, default
#      /mnt/usb-8tb, on the device of /) or a source outside it, a destination with less free
#      room than --min-free-gb (default 40, in 10^9 bytes) or than the source, a non-empty
#      destination without --resume, and an existing receipt of the edition (never rewritten);
#   2. lists the source (path, size, mtime), hashes every file, copies with rsync
#      -a --partial --inplace when present, else tar (ARCHIVE_COPY_TRANSPORT=auto|rsync|tar);
#   3. lists the source again: any change of size or mtime during the transfer is `UNSTABLE
#      <path>` and exit 82, no manifest, no receipt;
#   4. hashes every file of the destination and compares with the source manifest: `MISMATCH`,
#      `MISSING` or `EXTRA <path>` and exit 81, no receipt;
#   5. writes <dest>/.archive-copy/manifest-<edition>.sha256 (one line per file:
#      `<sha256> <size> <relative path>`, sorted) and receipt-<edition>.json (date UTC, source,
#      destination, edition, files, bytes, manifest and its SHA-256, transport, duration), then
#      prints `ARCHIVE_COPY_OK`. `.archive-copy/` starts with a dot: app.etl.source_archive
#      ignores it, so the copy stays a readable archive (SOURCE_ARCHIVE_DIR=<copy>).
# --verify-only: hashes the destination again, compares with its manifest (whose SHA-256 must
#   match the receipt), prints `ARCHIVE_VERIFY_OK` or names each difference (81).
# --dry-run: read-only checks, then prints what would run; writes nothing anywhere.
# Exit codes: 0 ok, 2 usage, 80 refused before any write, 81 copy differs, 82 unstable source.
# File names holding a newline, a tab or a backslash are refused (80): the manifest is line based.
set -euo pipefail

readonly SENTINEL_NAME=.datacommune-source-archive
readonly SENTINEL_CONTENT="datacommune-source-archive v1"
readonly META=.archive-copy

usage() {
  echo "usage: $0 --source <dir> --dest <user@host:dir|dir> --edition <label> [--min-free-gb <n>] [--usb-mount <dir|none>] [--dry-run] [--resume]" >&2
  echo "       $0 --dest <user@host:dir|dir> --edition <label> --verify-only" >&2
  exit 2
}
refuse() { echo "REFUSED: $*" >&2; exit 80; }

source_dir="" dest="" edition="" min_free_gb=40 usb_mount=/mnt/usb-8tb
dry_run=0 verify_only=0 resume=0
while [ $# -gt 0 ]; do
  case "$1" in
    --source) source_dir="${2:-}"; shift 2 ;;
    --dest) dest="${2:-}"; shift 2 ;;
    --edition) edition="${2:-}"; shift 2 ;;
    --min-free-gb) min_free_gb="${2:-}"; shift 2 ;;
    --usb-mount) usb_mount="${2:-}"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --verify-only) verify_only=1; shift ;;
    --resume) resume=1; shift ;;
    *) usage ;;
  esac
done
[[ "$edition" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "--edition: [A-Za-z0-9._-] only" >&2; usage; }
[[ "$min_free_gb" =~ ^[0-9]+$ ]] || { echo "--min-free-gb: integer" >&2; usage; }
[ -n "$dest" ] || usage
[ "$verify_only" = 1 ] || [ -n "$source_dir" ] || usage
# Paths reach a remote shell and the JSON receipt: a plain character set, no quoting games.
for p in "$source_dir" "$dest" "$usb_mount"; do
  case "$p" in *[!A-Za-z0-9._/@:+-]*) echo "unexpected character in '$p'" >&2; usage ;; esac
done

# Destination: user@host:/dir (remote) or a local directory.
dest_host="" dest_dir="$dest"
if [[ "$dest" =~ ^([A-Za-z0-9._-]+@[A-Za-z0-9._-]+):(/.*)$ ]]; then
  dest_host="${BASH_REMATCH[1]}" dest_dir="${BASH_REMATCH[2]}"
fi
manifest_name="manifest-$edition.sha256"
receipt_name="receipt-$edition.json"

# at_dest <script> [args...]: runs a bash script where the destination lives, stdin passed on.
at_dest() {
  local script="$1"; shift
  if [ -n "$dest_host" ]; then
    ssh -o BatchMode=yes "$dest_host" "bash -c $(printf '%q' "$script") _ $(printf '%q ' "$@")"
  else
    bash -c "$script" _ "$@"
  fi
}

# Hash list of a tree, metadata directory excluded: `<sha256> <size> <path>`, sorted by path.
# shellcheck disable=SC2016
readonly HASH_TREE='set -euo pipefail
cd -- "$1"
find . -path ./'"$META"' -prune -o -type f -printf "%P\n" | LC_ALL=C sort | while IFS= read -r f; do
  h="$(sha256sum -- "$f")" || exit 1
  printf "%s %s %s\n" "${h%% *}" "$(stat -c %s -- "$f")" "$f"
done'

# compare <expected manifest> <observed list>: prints each difference, exit 1 if any.
compare() {
  awk '
    { p = $0; sub(/^[^ ]+ [^ ]+ /, "", p) }
    NR == FNR { want[p] = $1 " " $2; next }
    { seen[p] = 1
      if (!(p in want)) { print "EXTRA " p; bad = 1 }
      else if (want[p] != $1 " " $2) { print "MISMATCH " p; bad = 1 } }
    END { for (p in want) if (!(p in seen)) { print "MISSING " p; bad = 1 }; exit bad }
  ' "$1" "$2"
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# --- verify only -------------------------------------------------------------------------------
if [ "$verify_only" = 1 ]; then
  # shellcheck disable=SC2016
  at_dest 'cat -- "$1/'"$META"'/$2"' "$dest_dir" "$manifest_name" >"$tmp/manifest" ||
    refuse "no manifest $manifest_name in $dest"
  # shellcheck disable=SC2016
  if at_dest 'cat -- "$1/'"$META"'/$2"' "$dest_dir" "$receipt_name" >"$tmp/receipt" 2>/dev/null; then
    want_sha="$(sed -n 's/.*"manifest_sha256": *"\([0-9a-f]*\)".*/\1/p' "$tmp/receipt")"
    got_sha="$(sha256sum "$tmp/manifest")"; got_sha="${got_sha%% *}"
    if [ "$want_sha" != "$got_sha" ]; then
      echo "MANIFEST_ALTERE $manifest_name sha256=$got_sha receipt=$want_sha"; exit 81
    fi
  fi
  at_dest "$HASH_TREE" "$dest_dir" >"$tmp/observed"
  if compare "$tmp/manifest" "$tmp/observed"; then
    echo "ARCHIVE_VERIFY_OK edition=$edition files=$(wc -l <"$tmp/manifest") dest=$dest"; exit 0
  fi
  exit 81
fi

# --- preconditions (read only) -------------------------------------------------------------------
[ -d "$source_dir" ] || refuse "source_absente $source_dir"
source_dir="$(cd "$source_dir" && pwd -P)"
[ -f "$source_dir/$SENTINEL_NAME" ] || refuse "sentinelle_absente $source_dir/$SENTINEL_NAME"
[ "$(cat "$source_dir/$SENTINEL_NAME")" = "$SENTINEL_CONTENT" ] || refuse "sentinelle_invalide $source_dir/$SENTINEL_NAME"
if [ "$usb_mount" != none ]; then
  [ -d "$usb_mount" ] || refuse "usb_non_monte $usb_mount absent"
  usb_dev="$(stat -c %D "$usb_mount")"
  [ "$usb_dev" != "$(stat -c %D /)" ] || refuse "usb_non_monte $usb_mount is on the device of / ($usb_dev)"
  case "$source_dir/" in "$(cd "$usb_mount" && pwd -P)"/*) ;; *) refuse "source_hors_usb $source_dir" ;; esac
fi
nl=$'\n' tab=$'\t'
odd="$(find "$source_dir" \( -name "*$nl*" -o -name "*$tab*" -o -name '*\\*' \) -print -quit)"
[ -z "$odd" ] || refuse "nom_de_fichier_refuse $odd"

snapshot() { (cd "$source_dir" && find . -type f -printf '%P\t%s\t%T@\n' | LC_ALL=C sort); }
snapshot >"$tmp/before"
source_bytes="$(awk -F'\t' '{ s += $2 } END { printf "%d", s }' "$tmp/before")"
need=$(( min_free_gb * 1000000000 ))
[ "$resume" = 1 ] || [ "$source_bytes" -le "$need" ] || need="$source_bytes"

# State of the destination: free bytes of its nearest existing directory, emptiness, receipt.
# shellcheck disable=SC2016
state="$(at_dest 'd="$1"; while [ ! -d "$d" ]; do d="$(dirname "$d")"; done
  free="$(df -B1 --output=avail "$d" | tail -1 | tr -d " ")"
  entries=0; [ -d "$1" ] && entries="$(find "$1" -mindepth 1 -maxdepth 1 | wc -l)"
  receipt=0; [ -e "$1/'"$META"'/$2" ] && receipt=1
  echo "$free $entries $receipt"' "$dest_dir" "$receipt_name" </dev/null)" || refuse "destination injoignable $dest"
read -r free entries has_receipt <<<"$state"
[ "$entries" = 0 ] || [ "$resume" = 1 ] || refuse "destination_non_vide $dest ($entries entries; --resume to complete it)"
[ "$has_receipt" = 0 ] || refuse "recu_existant $dest/$META/$receipt_name"
[ "$free" -ge "$need" ] || refuse "espace_insuffisant $dest: $free bytes free < $need required"

transport="${ARCHIVE_COPY_TRANSPORT:-auto}"
case "$transport" in
  auto) if command -v rsync >/dev/null 2>&1; then transport=rsync; else transport=tar; fi ;;
  rsync|tar) ;;
  *) echo "ARCHIVE_COPY_TRANSPORT: auto, rsync or tar" >&2; exit 2 ;;
esac
files="$(wc -l <"$tmp/before")"

if [ "$dry_run" = 1 ]; then
  echo "+ hash $files files of $source_dir ($source_bytes bytes)"
  echo "+ copy by $transport to $dest (free $free bytes, required $need)"
  echo "+ re-list the source (size, mtime), hash $dest, compare"
  echo "+ write $dest/$META/$manifest_name and $receipt_name"
  echo "DRY_RUN archive-copy edition=$edition"
  exit 0
fi

# --- copy --------------------------------------------------------------------------------------
started="$(date +%s)"
bash -c "$HASH_TREE" _ "$source_dir" >"$tmp/manifest"
# shellcheck disable=SC2016
at_dest 'mkdir -p -- "$1"' "$dest_dir" </dev/null
if [ "$transport" = rsync ]; then
  if [ -n "$dest_host" ]; then
    rsync -a --partial --inplace -e "ssh -o BatchMode=yes" -- "$source_dir/" "$dest_host:$dest_dir/" </dev/null
  else
    rsync -a --partial --inplace -- "$source_dir/" "$dest_dir/" </dev/null
  fi
else
  # shellcheck disable=SC2016
  tar -C "$source_dir" -cf - . | at_dest 'tar -C "$1" -xf -' "$dest_dir"
fi

snapshot >"$tmp/after"
if ! cmp -s "$tmp/before" "$tmp/after"; then
  # A path whose (size, mtime) line changed, appeared or vanished.
  diff "$tmp/before" "$tmp/after" | sed -n 's/^[<>] \([^\t]*\)\t.*/UNSTABLE \1/p' | sort -u || true
  echo "ARCHIVE_COPY_BAD source changed during the transfer: no manifest, no receipt" >&2
  exit 82
fi

at_dest "$HASH_TREE" "$dest_dir" </dev/null >"$tmp/observed"
if ! compare "$tmp/manifest" "$tmp/observed"; then
  echo "ARCHIVE_COPY_BAD the copy differs from the source: no receipt" >&2
  exit 81
fi

manifest_sha="$(sha256sum "$tmp/manifest")"; manifest_sha="${manifest_sha%% *}"
bytes="$(awk '{ s += $2 } END { printf "%d", s }' "$tmp/manifest")"
cat >"$tmp/receipt" <<EOF
{
  "date_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source": "$source_dir",
  "destination": "$dest",
  "edition": "$edition",
  "files": $files,
  "bytes": $bytes,
  "manifest": "$manifest_name",
  "manifest_sha256": "$manifest_sha",
  "transport": "$transport",
  "duration_s": $(( $(date +%s) - started ))
}
EOF
# The manifest first, the receipt last: a receipt always names a written manifest.
# shellcheck disable=SC2016
at_dest 'mkdir -p -- "$1/'"$META"'" && cat >"$1/'"$META"'/$2"' "$dest_dir" "$manifest_name" <"$tmp/manifest"
# shellcheck disable=SC2016
at_dest 'set -o noclobber; cat >"$1/'"$META"'/$2"' "$dest_dir" "$receipt_name" <"$tmp/receipt"
echo "ARCHIVE_COPY_OK edition=$edition files=$files bytes=$bytes manifest_sha256=$manifest_sha dest=$dest"
