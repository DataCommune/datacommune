#!/usr/bin/env bash
# Provision and harden a fresh Debian 12 rented server for DataCommune (plan 04.2-01).
#
#   sudo bash provision.sh [--dry-run] --ssh-key "<public key line>" --admin-ip <ip or cidr> \
#     --client-ip <address of this SSH session>
#
# Run ON the server, through sudo from the admin account, after copying this file and Ianis's
# PUBLIC key there (README.md). --client-ip is "${SSH_CLIENT%% *}", expanded by the admin
# shell before sudo (sudo resets the environment). Idempotent: every step checks the current
# state and changes only what differs, so a second run changes nothing. --dry-run prints every
# action and executes no system command (it reads only $OS_RELEASE_FILE and the admin account's
# authorized_keys).
#
# Lockout guards, before anything changes (CR-01, WR-01 of 04.2-REVIEW-CIBLEE.md): --client-ip
# must be inside --admin-ip and of the same IP family, and the account that ran sudo
# ($SUDO_USER, not root) must already hold the same key -- password and root logins go off
# below and the OVH KVM console needs a password nobody has.
#
# In order:
#   1. refuses any OS but Debian 12 (read from ${OS_RELEASE_FILE:-/etc/os-release}: the
#      only override, used by the tests with a fake file);
#   2. apt packages: ufw, fail2ban, unattended-upgrades, openssl, curl, gnupg;
#   3. user `datacommune` with UID 1000 -- the UID of `appuser` in api/docker/Dockerfile
#      (first `adduser` of the image), which writes /sources; refused if UID 1000 belongs to
#      another account (README, "UID 1000");
#   4. the given public key in ~datacommune/.ssh/authorized_keys; only once it is there:
#      sshd drop-in PasswordAuthentication no, KbdInteractiveAuthentication no,
#      PermitRootLogin no (validated by `sshd -t` before reload);
#   5. fail2ban (sshd jail, systemd backend);
#   6. unattended-upgrades limited to Debian security, automatic reboot disabled;
#   7. Docker Engine + compose plugin from Docker's apt repository, signing key fingerprint
#      checked (same check as the hypervisor's provision-workbase.sh); datacommune in group docker;
#   8. /opt/datacommune 0700 with releases, imports, backups (0700) and sources (0750),
#      owner datacommune; sentinel sources/.datacommune-source-archive containing exactly
#      `datacommune-source-archive v1` (without it every import fails: sentinelle_absente);
#   9. 4 GiB swap file when no swap is active;
#  10. journald bounded (SystemMaxUse=2G);
#  11. `tune2fs -m 1` on the root filesystem, only if it is ext4 and reserves more than 1 %;
#  12. last, so that its 10 minutes start when nothing else is left to run: ufw, deny incoming
#      by default, SSH (22/tcp) from --admin-ip only, no 80/443 (HTTP and HTTPS arrive through
#      the Cloudflare Tunnel, an outbound connection); dead man's switch: a transient timer
#      runs `ufw disable` after 10 minutes unless a NEW session cancels it
#      (`systemctl stop dc-ufw-deadman.timer`, printed right after, README.md § 3).
# Docker publishes only 127.0.0.1:8080 (caddy, compose): ufw does not see Docker's own
# rules, which is why no port but loopback is ever published by the stack.
#
# No secret is handled here: /opt/datacommune/.env is written by the private bootstrap
# block of README.md, never by this script.
#
# Exit codes: 0 provisioned (or dry run printed) | 1 a system step failed | 2 usage |
# 80 refused, nothing changed by the refused step (client address, admin account, OS, UID,
# key, sentinel).
# Last line: `PROVISIONED ...` (or `DRY_RUN provision ...`).
set -euo pipefail

readonly DOCKER_KEY_FPR=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
readonly APP_USER=datacommune
readonly APP_UID=1000
readonly BASE=/opt/datacommune
readonly SENTINEL_NAME=.datacommune-source-archive
readonly SENTINEL_CONTENT="datacommune-source-archive v1"
readonly SWAP_FILE=/swapfile
readonly SWAP_SIZE=4G
OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"

usage() {
  echo "usage: $0 [--dry-run] --ssh-key \"<public key line>\" --admin-ip <ip or cidr> --client-ip <ip>" >&2
  exit 2
}
refuse() { echo "REFUSED: $*" >&2; exit 80; }

dry_run=0 ssh_key="" admin_ip="" client_ip=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1; shift ;;
    --ssh-key) ssh_key="${2:-}"; shift 2 || usage ;;
    --admin-ip) admin_ip="${2:-}"; shift 2 || usage ;;
    --client-ip) client_ip="${2:-}"; shift 2 || usage ;;
    -h|--help) sed -n '2,53p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done
[ -n "$ssh_key" ] || { echo "--ssh-key is required (Ianis's public key line)" >&2; usage; }
[ -n "$admin_ip" ] || { echo "--admin-ip is required (address allowed to reach SSH)" >&2; usage; }
[ -n "$client_ip" ] || { echo "--client-ip is required (address of this SSH session: \"\${SSH_CLIENT%% *}\", README.md § 3)" >&2; usage; }

# One public key line: type, base64 body, optional comment; no newline, no options prefix.
[[ "$ssh_key" =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com)\ [A-Za-z0-9+/]+=*(\ [^[:cntrl:]]*)?$ ]] ||
  refuse "--ssh-key is not a single OpenSSH public key line"
[[ "$admin_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}(/([0-9]|[12][0-9]|3[0-2]))?$ ]] ||
  [[ "$admin_ip" =~ ^[0-9A-Fa-f:]+(/[0-9]{1,3})?$ && "$admin_ip" == *:* ]] ||
  refuse "--admin-ip '$admin_ip' is not an IPv4/IPv6 address or prefix"
[[ "$client_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || [[ "$client_ip" =~ ^[0-9A-Fa-f:]+$ && "$client_ip" == *:* ]] ||
  refuse "--client-ip '$client_ip' is not a single IPv4/IPv6 address"

# The session running this script must still get in once ufw admits --admin-ip only.
ip_family() { case "$1" in *:*) echo 6 ;; *) echo 4 ;; esac; }
ip4_int() { # ip4_int <a.b.c.d>: the address as an integer (decimal octets, 10# against octal)
  local IFS=. a b c d o
  read -r a b c d <<< "$1"
  for o in "$a" "$b" "$c" "$d"; do [ "$((10#$o))" -le 255 ] || return 1; done
  echo $(( (10#$a << 24) | (10#$b << 16) | (10#$c << 8) | 10#$d ))
}
in_admin_net() { # in_admin_net <address> <address or cidr>, same family
  local net="${2%/*}" len a n mask
  if [ "$(ip_family "$1")" = 6 ]; then
    command -v python3 >/dev/null 2>&1 || refuse "IPv6 --client-ip: python3 is needed to compare it with --admin-ip"
    python3 -c 'import ipaddress, sys; sys.exit(0 if ipaddress.ip_address(sys.argv[1]) in ipaddress.ip_network(sys.argv[2], strict=False) else 1)' "$1" "$2"
    return
  fi
  len=32; [ "$2" = "$net" ] || len="${2#*/}"
  a="$(ip4_int "$1")" && n="$(ip4_int "$net")" || return 1
  mask=$(( len == 0 ? 0 : (0xFFFFFFFF << (32 - len)) & 0xFFFFFFFF ))
  [ "$(( a & mask ))" = "$(( n & mask ))" ]
}
[ "$(ip_family "$client_ip")" = "$(ip_family "$admin_ip")" ] ||
  refuse "--client-ip $client_ip is IPv$(ip_family "$client_ip"), --admin-ip $admin_ip is IPv$(ip_family "$admin_ip"): ufw would refuse the next connection (connect over the family of --admin-ip, or give an --admin-ip of this family)"
in_admin_net "$client_ip" "$admin_ip" ||
  refuse "--client-ip $client_ip is not inside --admin-ip $admin_ip: ufw would refuse the next SSH connection"

# --- 1. OS ---------------------------------------------------------------------
[ -r "$OS_RELEASE_FILE" ] || refuse "$OS_RELEASE_FILE unreadable"
os_field() { sed -n "s/^$1=//p" "$OS_RELEASE_FILE" | head -1 | tr -d '"'"'"; }
os_id="$(os_field ID)" os_version="$(os_field VERSION_ID)"
[ "$os_id" = debian ] && [ "$os_version" = 12 ] ||
  refuse "Debian 12 required, found ID=${os_id:-?} VERSION_ID=${os_version:-?} ($OS_RELEASE_FILE)"

if [ "$dry_run" = 0 ] && [ "$(id -u)" != 0 ]; then
  refuse "run as root (sudo bash $0 ...)"
fi

# Password and root logins go off below: the account that ran sudo must keep a key-based way
# in with this same key (datacommune gets the key too, but has no sudo). Read-only, also in
# --dry-run.
admin_user="${SUDO_USER:-}"
[ -n "$admin_user" ] && [ "$admin_user" != root ] ||
  refuse "run through sudo from the non-root admin account (SUDO_USER is '${admin_user:-empty}'): PermitRootLogin no would cut the only admin access"
admin_home="$(getent passwd "$admin_user" | cut -d: -f6)" || admin_home=""
key_body="$(printf '%s' "$ssh_key" | cut -d' ' -f2)"
# One awk over the whole file (a `grep | grep -q` pipe can fail on SIGPIPE under pipefail);
# commented-out lines do not count.
[ -n "$admin_home" ] && awk -v k="$key_body" '!/^[[:space:]]*#/ && index($0, k) {found = 1} END {exit !found}' \
  "$admin_home/.ssh/authorized_keys" 2>/dev/null ||
  refuse "admin account $admin_user keeps no SSH access with this key (not in ${admin_home:-<no home>}/.ssh/authorized_keys): add it there before password and root logins go off"

# run <command...>: printed under --dry-run, executed otherwise.
run() {
  if [ "$dry_run" = 1 ]; then printf '+ %s\n' "$*"; return 0; fi
  "$@"
}
# put_file <path> <mode> <owner:group> <content>: writes only when the content differs.
put_file() {
  local path="$1" mode="$2" owner="$3" content="$4"
  if [ "$dry_run" = 1 ]; then
    printf '+ write %s (mode %s, %s):\n' "$path" "$mode" "$owner"
    printf '%s\n' "$content" | sed 's/^/    | /'
    return 0
  fi
  if [ -f "$path" ] && [ "$(cat "$path")" = "$content" ]; then
    echo "unchanged: $path" >&2
  else
    local tmp
    tmp="$(mktemp "$(dirname "$path")/.provision.XXXXXX")"
    printf '%s\n' "$content" >"$tmp"
    chmod "$mode" "$tmp"; chown "$owner" "$tmp"
    mv -f "$tmp" "$path"
    echo "written: $path" >&2
  fi
  chmod "$mode" "$path"; chown "$owner" "$path"
}
note() { printf '# %s\n' "$*"; }

export DEBIAN_FRONTEND=noninteractive

# --- 2. packages ----------------------------------------------------------------
note "packages"
run apt-get update -qq
run apt-get install -y -qq ca-certificates curl gnupg openssl ufw fail2ban unattended-upgrades

# --- 3. user, UID 1000 ----------------------------------------------------------
note "user $APP_USER, UID $APP_UID (= appuser of the API image, owner of $BASE/sources)"
if [ "$dry_run" = 1 ]; then
  printf '+ adduser --disabled-password --gecos "" --uid %s %s  # if missing; refused if UID %s is another account\n' \
    "$APP_UID" "$APP_USER" "$APP_UID"
else
  if ! id "$APP_USER" >/dev/null 2>&1; then
    holder="$(getent passwd "$APP_UID" | cut -d: -f1 || true)"
    [ -z "$holder" ] || refuse "UID $APP_UID already belongs to '$holder'; $APP_USER must have UID $APP_UID (README, \"UID 1000\")"
    adduser --disabled-password --gecos "" --uid "$APP_UID" "$APP_USER" >/dev/null
  fi
  [ "$(id -u "$APP_USER")" = "$APP_UID" ] ||
    refuse "$APP_USER has UID $(id -u "$APP_USER"), expected $APP_UID (README, \"UID 1000\")"
fi

# --- 4. SSH key, then sshd hardening ---------------------------------------------
note "SSH: key of Ianis for $APP_USER, then password authentication off"
home="/home/$APP_USER"
run install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$home/.ssh"
auth="$home/.ssh/authorized_keys"
if [ "$dry_run" = 1 ]; then
  printf '+ append the --ssh-key line to %s (mode 600) if absent\n' "$auth"
else
  touch "$auth"
  grep -qxF -- "$ssh_key" "$auth" || printf '%s\n' "$ssh_key" >>"$auth"
  chmod 600 "$auth"; chown "$APP_USER:$APP_USER" "$auth"
  grep -qxF -- "$ssh_key" "$auth" || refuse "the key is not in $auth: password authentication left as is"
fi
put_file /etc/ssh/sshd_config.d/10-datacommune.conf 0644 root:root \
"# Written by infra/prod/provision.sh. First match wins: this file sorts before 50-*.conf.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes"
run sshd -t
run systemctl reload ssh

# --- 5. fail2ban --------------------------------------------------------------------
note "fail2ban"
put_file /etc/fail2ban/jail.d/datacommune-sshd.local 0644 root:root \
"[sshd]
enabled = true
backend = systemd"
run systemctl enable fail2ban
run systemctl restart fail2ban

# --- 6. security updates --------------------------------------------------------------
note "unattended-upgrades: security only, no automatic reboot"
put_file /etc/apt/apt.conf.d/20auto-upgrades 0644 root:root \
'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";'
put_file /etc/apt/apt.conf.d/52datacommune-unattended-upgrades 0644 root:root \
'// Written by infra/prod/provision.sh: Debian security only (no point release, no Docker).
#clear Unattended-Upgrade::Origins-Pattern;
Unattended-Upgrade::Origins-Pattern {
  "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
  "origin=Debian,codename=${distro_codename},label=Debian-Security";
};
Unattended-Upgrade::Automatic-Reboot "false";'
run systemctl enable unattended-upgrades

# --- 7. Docker --------------------------------------------------------------------------
note "Docker Engine + compose plugin (signing key $DOCKER_KEY_FPR checked)"
if [ "$dry_run" = 1 ]; then
  printf '+ fetch https://download.docker.com/linux/debian/gpg, accept only one key with fingerprint %s\n' "$DOCKER_KEY_FPR"
  printf '+ write /etc/apt/sources.list.d/docker.list (bookworm stable)\n'
else
  docker_key_ok() {
    local listing pubs first
    listing="$(gpg --show-keys --with-colons "$1" 2>/dev/null)" || return 1
    pubs="$(printf '%s\n' "$listing" | grep -c '^pub:' || true)"
    first="$(printf '%s\n' "$listing" | awk -F: '$1 == "fpr" {print $10; exit}')"
    [ "$pubs" -eq 1 ] && [ "$first" = "$2" ]
  }
  install -m 0755 -d /etc/apt/keyrings
  tmp_key="$(mktemp)"
  curl -fsSL https://download.docker.com/linux/debian/gpg -o "$tmp_key"
  docker_key_ok "$tmp_key" "$DOCKER_KEY_FPR" ||
    { rm -f "$tmp_key"; refuse "docker signing key: not exactly one key with fingerprint $DOCKER_KEY_FPR"; }
  install -m 0644 "$tmp_key" /etc/apt/keyrings/docker.asc
  rm -f "$tmp_key"
  codename="$(os_field VERSION_CODENAME)"
  put_file /etc/apt/sources.list.d/docker.list 0644 root:root \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${codename:-bookworm} stable"
fi
run apt-get update -qq
run apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
run systemctl enable --now docker
run usermod -aG docker "$APP_USER"

# --- 8. directories and source archive sentinel ----------------------------------------------
note "$BASE (0700), releases, imports, backups (0700), sources (0750), owner $APP_USER"
run install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$BASE" "$BASE/releases" "$BASE/backups"
run install -d -m 0750 -o "$APP_USER" -g "$APP_USER" "$BASE/imports" "$BASE/sources"
sentinel="$BASE/sources/$SENTINEL_NAME"
note "sentinel $sentinel = '$SENTINEL_CONTENT' (without it every import fails: sentinelle_absente)"
if [ "$dry_run" = 1 ]; then
  printf '+ write %s containing "%s" if absent (owner %s, mode 640); refused if it holds anything else\n' \
    "$sentinel" "$SENTINEL_CONTENT" "$APP_USER"
else
  if [ -e "$sentinel" ]; then
    # The reader strips the content before comparing it (app/etl/source_archive.py).
    [ "$(tr -d '[:space:]' <"$sentinel")" = "$(printf '%s' "$SENTINEL_CONTENT" | tr -d '[:space:]')" ] ||
      refuse "$sentinel exists with another content: not overwritten, check what this directory is"
  else
    put_file "$sentinel" 0640 "$APP_USER:$APP_USER" "$SENTINEL_CONTENT"
  fi
fi

# --- 9. swap -----------------------------------------------------------------------------------
note "swap $SWAP_SIZE when no swap is active"
if [ "$dry_run" = 1 ]; then
  printf '+ fallocate -l %s %s; chmod 600; mkswap; swapon; /etc/fstab entry  # only if no swap is active\n' "$SWAP_SIZE" "$SWAP_FILE"
elif [ -z "$(swapon --show --noheadings)" ]; then
  [ -e "$SWAP_FILE" ] || { fallocate -l "$SWAP_SIZE" "$SWAP_FILE"; chmod 600 "$SWAP_FILE"; mkswap "$SWAP_FILE" >/dev/null; }
  swapon "$SWAP_FILE"
  grep -qE "^$SWAP_FILE[[:space:]]" /etc/fstab || printf '%s none swap sw 0 0\n' "$SWAP_FILE" >>/etc/fstab
else
  echo "unchanged: swap already active" >&2
fi

# --- 10. journald -------------------------------------------------------------------------------
note "journald SystemMaxUse=2G"
run install -d -m 0755 /etc/systemd/journald.conf.d
put_file /etc/systemd/journald.conf.d/50-datacommune.conf 0644 root:root \
"[Journal]
SystemMaxUse=2G"
run systemctl restart systemd-journald

# --- 11. ext4 reserved blocks ---------------------------------------------------------------------
note "tune2fs -m 1 on / if ext4 and above 1 %"
if [ "$dry_run" = 1 ]; then
  printf '+ tune2fs -m 1 <root device>  # only if findmnt -no FSTYPE / is ext4 and the reserved count is above 1 %%\n'
else
  fstype="$(findmnt -no FSTYPE /)"
  if [ "$fstype" = ext4 ]; then
    dev="$(findmnt -no SOURCE /)"
    blocks="$(tune2fs -l "$dev" | sed -n 's/^Block count:[[:space:]]*//p')"
    reserved="$(tune2fs -l "$dev" | sed -n 's/^Reserved block count:[[:space:]]*//p')"
    if [ "$((reserved * 100))" -gt "$blocks" ]; then
      tune2fs -m 1 "$dev" >/dev/null
      echo "tune2fs: $dev reserved blocks set to 1 %" >&2
    else
      echo "unchanged: $dev reserves 1 % or less" >&2
    fi
  else
    echo "unchanged: root filesystem is ${fstype:-unknown}, not ext4" >&2
  fi
fi

# --- 12. firewall, last ------------------------------------------------------------------------
# Last step (WR-06 of 04.2-REVIEW-CIBLEE.md): the 10 minutes of the switch start when nothing
# but this step is left, and the cancel instruction comes right after.
note "ufw: deny incoming, SSH from $admin_ip only, no 80/443"
run ufw default deny incoming
run ufw default allow outgoing
run ufw allow from "$admin_ip" to any port 22 proto tcp
# Dead man's switch, re-armed at every run: if the next connection is refused anyway, ufw
# switches itself off after 10 minutes (key-only SSH and fail2ban stay). A NEW session that
# gets in cancels it (README.md § 3).
if [ "$dry_run" = 1 ]; then
  printf '+ systemctl stop dc-ufw-deadman.timer  # the switch of a previous run, if any\n'
  printf '+ systemd-run --unit=dc-ufw-deadman --on-active=10min /usr/sbin/ufw disable\n'
else
  systemctl stop dc-ufw-deadman.timer >/dev/null 2>&1 || true
  systemctl reset-failed dc-ufw-deadman.timer dc-ufw-deadman.service >/dev/null 2>&1 || true
  systemd-run --quiet --unit=dc-ufw-deadman --on-active=10min /usr/sbin/ufw disable
fi
run ufw --force enable
echo "NEXT, within 10 minutes: from the workstation, a NEW session 'ssh $admin_user@<server> true', then 'ssh $admin_user@<server> sudo systemctl stop dc-ufw-deadman.timer' (README.md § 3)" >&2
if [ "$dry_run" = 1 ]; then
  echo "DRY_RUN provision user=$APP_USER uid=$APP_UID admin_ip=$admin_ip client_ip=$client_ip admin=$admin_user os=debian-12"
else
  echo "PROVISIONED user=$APP_USER uid=$(id -u "$APP_USER") admin_ip=$admin_ip sentinel=$(cat "$sentinel")"
fi
