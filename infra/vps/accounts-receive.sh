#!/usr/bin/env bash
# Forced command of the receiving account of the VPS (plan 05-01, D-06, D-08). Installed as
# /usr/local/bin/dc-accounts-receive, owner root, mode 755; account `dcbackup` without
# password, one line in ~dcbackup/.ssh/authorized_keys:
#   restrict,command="/usr/local/bin/dc-accounts-receive" ssh-ed25519 ... dc-accounts-vps
# The hypervisor runs `ssh <alias> <name> < archive`: the name arrives in SSH_ORIGINAL_COMMAND, the
# archive on stdin. The archive is already encrypted by age on the production host: this
# account never sees a clear byte and holds no key.
#
# Refused (exit 2, nothing written): a name other than accounts-<8 digits>T<6 digits>Z.tar.age,
# an empty input, an input without the age header. Otherwise written as a hidden .partial in
# $DC_ACCOUNTS_DIR (default /srv/dc-accounts), renamed, then only the 30 newest archives of
# that pattern are kept.
# Last line: RECEIVED <name> bytes=<n> kept=<n> | REFUSED <reason>
set -euo pipefail
umask 077

dir="${DC_ACCOUNTS_DIR:-/srv/dc-accounts}"
keep="${DC_ACCOUNTS_KEEP:-30}"
name="${SSH_ORIGINAL_COMMAND:-${1:-}}"
refuse() { echo "REFUSED $1"; exit 2; }

[[ "$name" =~ ^accounts-[0-9]{8}T[0-9]{6}Z\.tar\.age$ ]] || refuse "name"
[[ "$keep" =~ ^[1-9][0-9]*$ ]] || refuse "keep"
[ -d "$dir" ] || refuse "directory"

partial="$dir/.$name.partial"
trap 'rm -f "$partial"' EXIT
cat >"$partial"
[ -s "$partial" ] || refuse "empty"
[ "$(head -c 21 "$partial")" = "age-encryption.org/v1" ] || refuse "header"
mv -f "$partial" "$dir/$name"
bytes="$(wc -c <"$dir/$name" | tr -d ' ')"

find "$dir" -maxdepth 1 -type f -name 'accounts-*.tar.age' -printf '%f\n' |
  grep -E '^accounts-[0-9]{8}T[0-9]{6}Z\.tar\.age$' | sort -r | tail -n +"$((keep + 1))" |
  while IFS= read -r old; do rm -f "$dir/$old"; done
kept="$(find "$dir" -maxdepth 1 -type f -name 'accounts-*.tar.age' | wc -l | tr -d ' ')"
echo "RECEIVED $name bytes=$bytes kept=$kept"
