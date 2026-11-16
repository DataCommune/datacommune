#!/usr/bin/env bash
# Prove the migration sequence and the way back of public-deploy.sh (WR-02 of
# 04.1-REVIEW-CIBLEE.md) without the hypervisor, without ssh and without network.
#
# The script under test is copied with lib.sh and tests/public.test.env (as public.local.env)
# into a scratch git
# repository whose commits A, B and C carry api/ and frontend/. Fake ssh, scp
# and pct come first in PATH: ssh runs the command in a local bash, `pct exec`
# runs the CT block there, `pct push` does nothing; stat and install ignore
# file modes. The releases are staged in
# PUBLIC_DIR beforehand (.complete), each with the migration files of its
# schema: A and C have head 105_dvf_adresse_suffixe, B has head
# 106_eligibilite_fixe_par_immeuble. Fake docker stands for compose and the
# containers: one api container (running or stopped, with the tag and sha of
# the release that started it), a db always healthy holding the revision of
# the base, `alembic upgrade head` moving it to the head of the image and
# FAILING if an api container is running (the old code would serve while the
# schema changes); fake curl answers the smoke for the running api only.
# Scenarios (exit codes of the header of public-deploy.sh):
#   (a) nominal A -> B (105 -> 106)    -> 0, api stopped before the upgrade,
#       base at 106, current B, previous A
#   (b) upgrade fails                  -> 81, base at 105, old api A started
#       again and named running
#   (c) upgrade fails, old api does not come back -> 84 UPGRADE_FAILED_API_DOWN,
#       conduct names the served commit A
#   (d) B unhealthy after migrating to 106 -> 87 MIGRATED_UNHEALTHY, A never
#       started again, never ROLLED_BACK, the pre-<B12> dump named
#   (e) C unhealthy, same schema as A (upgrade is a no-op) -> 90 ROLLED_BACK,
#       A serving
#   (f) --rollback from B to A with the base at 106 -> 80, nothing started,
#       B still serving
#   (g) --rollback from C to A with the base at 105 -> 0, A serving
#   (h) build fails                    -> 81, api never stopped
#   (i) docker stop leaves the api running -> 81, nothing migrated
#   (j) first deployment (no api, no previous) failing its upgrade -> 81,
#       "no api was running"
#   (k) the 04.2-05 incident: database of 144 GB, 18 GB free for the pre-deploy dump
#       -> 80 before anything stops: no dump, api never stopped, nothing migrated
#   (l) that same base on a ZFS clone (CT pgdata mounted from dcpool/public/pg-*) -> 0,
#       no dump: snapshot <clone>@pre-<sha12>-<date> taken before the api stops, printed
#       and written as pre-<sha12>-<date>.ref; (l2) --backup-ref file:<dump> -> 0, no
#       dump, no snapshot, the reference written; (l3) on a clone, a refusal of the CT
#       block (CF_TUNNEL_TOKEN missing, base not restored) -> 80 and no snapshot, and
#       --dry-run reads nothing remote; (l4) zfs unreadable for a
#       clone mount -> 80, neither snapshot nor dump
#   (m) the dump fails, or has no PGDMP header -> 81, the partial file deleted, api never
#       stopped; a .part left by a killed dump is removed before the space guard
#   (n) --backup-ref malformed (zfs:, relative) or with --rollback, factor of the guard 0,5
#       or 0 -> 2 usage
# Prints PUBLIC_DEPLOY_OK only if every scenario passes; otherwise names the
# failing scenario(s) and exits 1.
#
# DEPLOY_GROUP=nominal|rollback|backup|usage|publication
# plays one group of scenarios; the last but one line names the scenarios played
# (SCENARIOS a b ...).
#
# PUBLIC_DEPLOY_SCRIPT overrides the script under test (used to show that this
# test turns red on the script before the fix).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
DEPLOY_UNDER_TEST="${PUBLIC_DEPLOY_SCRIPT:-$REPO/infra/public/public-deploy.sh}"
R105=105_dvf_adresse_suffixe
R106=106_eligibilite_fixe_par_immeuble

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export SIM="$WORK/sim" FAKE_LOG="$WORK/calls.log"
FAKE_BIN="$WORK/bin"
GIT_REPO="$WORK/repo"
mkdir -p "$FAKE_BIN" "$GIT_REPO/infra/public" "$GIT_REPO/api" "$GIT_REPO/frontend"
cp "$DEPLOY_UNDER_TEST" "$GIT_REPO/infra/public/public-deploy.sh"
cp "$REPO/infra/public/lib.sh" "$GIT_REPO/infra/public/"
cp "$REPO/infra/public/tests/public.test.env" "$GIT_REPO/infra/public/public.local.env"
SCRIPT="$GIT_REPO/infra/public/public-deploy.sh"

commit() { # commit <label>: prints the sha
  echo "$1" > "$GIT_REPO/api/release"; echo "$1" > "$GIT_REPO/frontend/release"
  git -C "$GIT_REPO" add api frontend >/dev/null
  git -C "$GIT_REPO" -c user.name=t -c user.email=t@t commit -q -m "$1" >/dev/null
  git -C "$GIT_REPO" rev-parse HEAD
}
git -C "$GIT_REPO" init -q
git -C "$GIT_REPO" config core.autocrlf false
A="$(commit A)"; B="$(commit B)"; C="$(commit C)"
# The public master (D-135): every commit here is on it, unless a case moves it.
PUBLISHED=refs/remotes/public/master
git -C "$GIT_REPO" update-ref "$PUBLISHED" "$C"

# --- doubles ---------------------------------------------------------------
cat > "$FAKE_BIN/ssh" <<'EOF'
#!/usr/bin/env bash
while [ "$#" -gt 0 ]; do case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
shift  # host
echo "ssh $1" | cut -c1-40 >> "$FAKE_LOG"
exec bash -c "$1"
EOF
cat > "$FAKE_BIN/scp" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$FAKE_BIN/pct" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  push) exit 0 ;;
  exec) shift 3; exec "$@" ;;
  config) cat "$SIM/ct-config" 2>/dev/null; exit 0 ;;
esac
exit 1
EOF
# Modes are not portable to the Windows workstations that run this test.
cat > "$FAKE_BIN/stat" <<'EOF'
#!/usr/bin/env bash
echo 600
EOF
cat > "$FAKE_BIN/install" <<'EOF'
#!/usr/bin/env bash
dirs=()
while [ "$#" -gt 0 ]; do case "$1" in -d) shift ;; -m) shift 2 ;; *) dirs+=("$1"); shift ;; esac; done
mkdir -p "${dirs[@]}"
EOF
cat > "$FAKE_BIN/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$FAKE_BIN/seq" <<'EOF'
#!/usr/bin/env bash
printf '1\n2\n3\n'
EOF
# $SIM/api: "<running|stopped> <tag> <sha>" or absent; $SIM/rev: revision of the base;
# $SIM/heads/<tag>: head revision of the image of <tag>.
cat > "$FAKE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
api_state() { [ -f "$SIM/api" ] && cut -d' ' -f"$1" "$SIM/api" || true; }
healthy() { [ "$(api_state 1)" = running ] && case " ${FAKE_UNHEALTHY:-} " in *" $(api_state 2) "*) false ;; *) true ;; esac; }
case "$1" in
  compose)
    shift
    while [ "$#" -gt 0 ]; do case "$1" in -p|--env-file|-f) shift 2 ;; *) break ;; esac; done
    case "$1" in
      build)
        echo "build $IMAGE_TAG" >> "$FAKE_LOG"; [ "${FAKE_BUILD_FAIL:-0}" = 1 ] && exit 1; exit 0 ;;
      up)
        case " $* " in *" db "*) echo "up db" >> "$FAKE_LOG"; exit 0 ;; esac
        echo "up $IMAGE_TAG" >> "$FAKE_LOG"
        echo "running $IMAGE_TAG $DEPLOYMENT_SHA" > "$SIM/api"; exit 0 ;;
      run)
        echo "upgrade $IMAGE_TAG" >> "$FAKE_LOG"
        if [ "$(api_state 1)" = running ]; then echo "UPGRADE_WHILE_API_RUNNING" >> "$FAKE_LOG"; exit 1; fi
        [ "${FAKE_UPGRADE_FAIL:-0}" = 1 ] && exit 1
        cp "$SIM/heads/$IMAGE_TAG" "$SIM/rev"; exit 0 ;;
    esac
    exit 1 ;;
  ps)
    case " $* " in
      *service=db*) echo db-ct ;;
      *service=api*) [ "$(api_state 1)" = running ] && echo api-ct ;;
      *service=frontend*) [ "$(api_state 1)" = running ] && echo fe-ct ;;
    esac
    exit 0 ;;
  inspect)
    c="${!#}"; { [ "$c" = db-ct ] || healthy; } && echo healthy || echo starting; exit 0 ;;
  exec)
    case " $* " in *pg_dump*)
      echo dump >> "$FAKE_LOG"
      [ "${FAKE_DUMP_NOHDR:-0}" = 1 ] && { echo XXXXX; exit 0; }
      echo PGDMP-partial; [ "${FAKE_DUMP_FAIL:-0}" = 1 ] && exit 1; echo DUMP; exit 0 ;; esac
    sql="$(cat)"
    case "$sql" in
      *to_regclass*) [ "${FAKE_NOT_RESTORED:-0}" = 1 ] && echo f || echo t ;; *version_num*) cat "$SIM/rev" ;;
      *pg_database_size*) echo "${FAKE_DB_SIZE:-5586488467}" ;;
    esac
    exit 0 ;;
  stop)
    echo "stop $(api_state 2)" >> "$FAKE_LOG"
    [ "${FAKE_STOP_NOOP:-0}" = 1 ] || sed -i 's/^running /stopped /' "$SIM/api"; exit 0 ;;
  start)
    echo "start $(api_state 2)" >> "$FAKE_LOG"
    sed -i 's/^stopped /running /' "$SIM/api"; exit 0 ;;
  images|rmi|image) exit 0 ;;
esac
exit 1
EOF
cat > "$FAKE_BIN/curl" <<'EOF'
#!/usr/bin/env bash
[ -f "$SIM/api" ] && [ "$(cut -d' ' -f1 "$SIM/api")" = running ] || exit 7
case " ${FAKE_UNHEALTHY:-} " in *" $(cut -d' ' -f2 "$SIM/api") "*) exit 22 ;; esac
case " $* " in
  *__version*) printf '{"deployment_sha":"%s"}\n' "$(cut -d' ' -f3 "$SIM/api")" ;;
  *" -sI "*) printf 'strict-transport-security: max-age=1\r\nx-robots-tag: noindex, nofollow\r\n' ;;
  *) echo ok ;;
esac
EOF
# Free bytes of the backups directory (df -B1 --output=avail).
cat > "$FAKE_BIN/df" <<'EOF2'
#!/usr/bin/env bash
printf 'Avail
%s
' "${FAKE_FREE:-39373615104}"
EOF2
# zfs: the dataset of a path is $FAKE_CLONE (none by default); snapshots logged.
cat > "$FAKE_BIN/zfs" <<'EOF2'
#!/usr/bin/env bash
case "$1" in
  list) [ "${FAKE_ZFS_FAIL:-0}" = 1 ] && exit 1; [ -n "${FAKE_CLONE:-}" ] && echo "$FAKE_CLONE"; exit 0 ;;
  snapshot) echo "zfs snapshot $2" >> "$FAKE_LOG"; exit 0 ;;
esac
exit 1
EOF2
chmod +x "$FAKE_BIN"/*

export PATH="$FAKE_BIN:$PATH"
export PUBLIC_SSH_HOST=fakehost PUBLIC_DIR="$WORK/ct/opt" PUBLIC_ENV_FILE="$WORK/ct/env"

# setup <base rev> <current sha> <previous sha|-> <api: sha|stopped|none>
setup() {
  rm -rf "$SIM" "$WORK/ct" "$FAKE_LOG"; mkdir -p "$SIM/heads" "$PUBLIC_DIR/releases"
  printf 'CF_TUNNEL_TOKEN=x\nPUBLIC_DC_API_KEY=dc_anon_test\n' > "$PUBLIC_ENV_FILE"
  echo "$1" > "$SIM/rev"
  [ "$2" = - ] || echo "$2" > "$PUBLIC_DIR/current_sha"
  [ "$3" = - ] || echo "$3" > "$PUBLIC_DIR/previous_sha"
  local s head
  for s in "$A" "$B" "$C"; do
    if [ "$s" = "$B" ]; then head=$R106; else head=$R105; fi
    echo "$head" > "$SIM/heads/${s:0:12}"
    local v="$PUBLIC_DIR/releases/${s:0:12}/api/backend/migrations/versions"
    mkdir -p "$v"
    printf 'revision: str = "%s"\ndown_revision: str = "104_dpe_rattachement_adresse_brute"\n' "$R105" > "$v/105.py"
    [ "$head" = $R105 ] || printf 'revision: str = "%s"\ndown_revision: str = "%s"\n' "$R106" "$R105" > "$v/106.py"
    touch "$PUBLIC_DIR/releases/${s:0:12}/.complete"
  done
  case "$4" in
    none) ;;
    *) echo "running ${4:0:12} $4" > "$SIM/api" ;;
  esac
  unset FAKE_UNHEALTHY FAKE_BUILD_FAIL FAKE_UPGRADE_FAIL FAKE_STOP_NOOP FAKE_DB_SIZE FAKE_FREE FAKE_DUMP_FAIL \
    FAKE_DUMP_NOHDR FAKE_CLONE FAKE_ZFS_FAIL FAKE_NOT_RESTORED PUBLIC_DUMP_SIZE_FACTOR
}
OUT="" RC=0
run() { OUT="$(bash "$SCRIPT" "$@" 2>&1)"; RC=$?; }
log_has() { grep -qx -- "$1" "$FAKE_LOG"; }
before() { # before <line1> <line2>: first <line1> precedes first <line2> in the log
  local a b
  a="$(grep -nx -m1 -- "$1" "$FAKE_LOG" | cut -d: -f1)"; b="$(grep -nx -m1 -- "$2" "$FAKE_LOG" | cut -d: -f1)"
  [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]
}
not_logged() { ! grep -q -- "$1" "$FAKE_LOG" 2>/dev/null; }
not_in_out() { ! grep -q -- "$1" <<<"$OUT"; }
api_is() { [ "$(cat "$SIM/api" 2>/dev/null)" = "$1" ]; }

FAILED=()
check() { # check <scenario> <condition...>
  local name="$1"; shift
  if ! "$@"; then FAILED+=("$name"); printf 'FAIL %s\n--- output (rc=%s)\n%s\n--- calls\n%s\n' "$name" "$RC" "$OUT" "$(cat "$FAKE_LOG" 2>/dev/null)" >&2; fi
}
a12="${A:0:12}" b12="${B:0:12}" c12="${C:0:12}"
# The clone of (l) to (l4) and the caller's dump of (l2) and (n): read by several groups.
CLONE=dcpool/public/pg-20261102T104052Z
REF=file:/mnt/usb-8tb/datacommune-backups/public-pre-national-20261102T104052Z.dump

# Groups (KI-19 shape): DEPLOY_GROUP=nominal|rollback|backup|usage|publication
# plays only that group, so the backend suite runs them side by side; unset, every
# scenario is played, as before.
# An unknown group fails: nothing is skipped silently.
RAN=()
case "${DEPLOY_GROUP:-}" in ""|nominal|rollback|backup|usage|publication) ;; *) echo "unknown DEPLOY_GROUP: $DEPLOY_GROUP" >&2; exit 2 ;; esac
group() { [ -z "${DEPLOY_GROUP:-}" ] || [ "$DEPLOY_GROUP" = "$1" ]; }
ran() { RAN+=("$1"); }

if group nominal; then
# (a) nominal A -> B
ran a
setup $R105 "$A" - "$A"; run "$B"
check a test "$RC" = 0
check a.order before "stop $a12" "upgrade $b12"
check a.no_overlap not_logged UPGRADE_WHILE_API_RUNNING
check a.rev test "$(cat "$SIM/rev")" = $R106
check a.state test "$(cat "$PUBLIC_DIR/current_sha") $(cat "$PUBLIC_DIR/previous_sha")" = "$B $A"
check a.out grep -q "DEPLOYED $B" <<<"$OUT"
check a.dump before dump "stop $a12"
check a.file ls "$PUBLIC_DIR"/backups/pre-"$b12"-*.pgdump
check a.no_part test -z "$(ls "$PUBLIC_DIR"/backups/*.part 2>/dev/null)"
check a.no_snap not_logged "^zfs snapshot"

# (b) upgrade fails: old api back
ran b
setup $R105 "$A" - "$A"; export FAKE_UPGRADE_FAIL=1; run "$B"
check b test "$RC" = 81
check b.api api_is "running $a12 $A"
check b.rev test "$(cat "$SIM/rev")" = $R105
check b.out grep -q "previous api api-ct running again" <<<"$OUT"
check b.order before "upgrade $b12" "start $a12"

# (c) upgrade fails, old api unhealthy after its start
ran c
setup $R105 "$A" - "$A"; export FAKE_UPGRADE_FAIL=1 FAKE_UNHEALTHY="$a12"; run "$B"
check c test "$RC" = 84
check c.out grep -q "UPGRADE_FAILED_API_DOWN.*do: infra/public/public-deploy.sh $A" <<<"$OUT"

# (d) B unhealthy after migrating to 106: A not restarted
ran d
setup $R105 "$A" - "$A"; export FAKE_UNHEALTHY="$b12"; run "$B"
check d test "$RC" = 87
check d.rev test "$(cat "$SIM/rev")" = $R106
check d.no_up_a not_logged "^up $a12"
check d.no_rollback not_in_out ROLLED_BACK
check d.dump grep -q "MIGRATED_UNHEALTHY.*restore .*/backups/pre-$b12-.*\.pgdump.*public-deploy.sh $A" <<<"$OUT"
check d.state test "$(cat "$PUBLIC_DIR/current_sha")" = "$A"

# (e) C unhealthy, schema unchanged: automatic return to A
ran e
setup $R105 "$A" - "$A"; export FAKE_UNHEALTHY="$c12"; run "$C"
check e test "$RC" = 90
check e.api api_is "running $a12 $A"
check e.out grep -q "ROLLED_BACK to $a12" <<<"$OUT"

fi

if group rollback; then
# (f) --rollback B -> A with the base at 106: refused
ran f
setup $R106 "$B" "$A" "$B"; run --rollback
check f test "$RC" = 80
check f.api api_is "running $b12 $B"
check f.no_up not_logged "^up "
check f.out grep -q "REFUSED: base at revision $R106, not the head of release $a12" <<<"$OUT"

# (g) --rollback C -> A with the base at 105
ran g
setup $R105 "$C" "$A" "$C"; run --rollback
check g test "$RC" = 0
check g.api api_is "running $a12 $A"

# (h) build fails: api never stopped
ran h
setup $R105 "$A" - "$A"; export FAKE_BUILD_FAIL=1; run "$B"
check h test "$RC" = 81
check h.no_stop not_logged "^stop "
check h.api api_is "running $a12 $A"

# (i) docker stop leaves the api running: nothing migrated
ran i
setup $R105 "$A" - "$A"; export FAKE_STOP_NOOP=1; run "$B"
check i test "$RC" = 81
check i.no_upgrade not_logged "^upgrade "
check i.rev test "$(cat "$SIM/rev")" = $R105

fi

if group backup; then
# (j) first deployment, no api, upgrade fails
ran j
setup $R105 - - none; export FAKE_UPGRADE_FAIL=1; run "$B"
check j test "$RC" = 81
check j.out grep -q "no api was running" <<<"$OUT"

# (k) incident of 04.2-05: refused before the cut
ran k
setup $R105 "$A" - "$A"; export FAKE_DB_SIZE=144000000000 FAKE_FREE=18000000000; run "$B"
check k test "$RC" = 80
check k.out grep -q "REFUSED: pre-deploy dump needs ~.* 18000000000 free" <<<"$OUT"
check k.no_dump not_logged "^dump"
check k.no_stop not_logged "^stop "
check k.no_upgrade not_logged "^upgrade "
check k.api api_is "running $a12 $A"
check k.rev test "$(cat "$SIM/rev")" = $R105

# (l) same base on a ZFS clone: snapshot of the clone
ran l
setup $R105 "$A" - "$A"; export FAKE_DB_SIZE=144000000000 FAKE_FREE=18000000000 FAKE_CLONE=$CLONE
printf 'memory: 12288\nmp0: /%s,mp=/var/lib/docker/volumes/datacommune-public_pgdata/_data\n' "$CLONE" > "$SIM/ct-config"
run "$B"
check l test "$RC" = 0
check l.no_dump not_logged "^dump"
check l.snap grep -q "^zfs snapshot $CLONE@pre-$b12-[0-9]\{8\}T[0-9]\{6\}Z\$" "$FAKE_LOG"
check l.snap_first before "$(grep -m1 '^zfs snapshot' "$FAKE_LOG")" "stop $a12"
check l.out grep -q "pre-deploy dump replaced by zfs:$CLONE@pre-$b12-" <<<"$OUT"
check l.ref grep -q "^zfs:$CLONE@pre-$b12-" "$(ls "$PUBLIC_DIR"/backups/pre-"$b12"-*.ref)"
check l.order before "stop $a12" "upgrade $b12"
# a mount from another dataset is not a clone of the switch: dumped
setup $R105 "$A" - "$A"; export FAKE_CLONE=dcpool/other
printf 'mp0: /dcpool/other,mp=/var/lib/docker/volumes/datacommune-public_pgdata/_data\n' > "$SIM/ct-config"
run "$B"
check l.other test "$RC" = 0
check l.other_dump log_has dump
check l.other_no_snap not_logged "^zfs snapshot"

# (l3) a refused deploy on a clone leaves no snapshot
ran l3
setup $R105 "$A" - "$A"; export FAKE_CLONE=$CLONE
printf 'mp0: /%s,mp=/var/lib/docker/volumes/datacommune-public_pgdata/_data\n' "$CLONE" > "$SIM/ct-config"
printf 'PUBLIC_DC_API_KEY=dc_anon_test\n' > "$PUBLIC_ENV_FILE"
run "$B"
check l3 test "$RC" = 80
check l3.no_snap not_logged "^zfs snapshot"
check l3.no_build not_logged "^build"
# ... nor a base not restored (refused after the build and the db)
setup $R105 "$A" - "$A"; export FAKE_CLONE=$CLONE FAKE_NOT_RESTORED=1
printf 'mp0: /%s,mp=/var/lib/docker/volumes/datacommune-public_pgdata/_data\n' "$CLONE" > "$SIM/ct-config"
run "$B"
check l3.restored test "$RC" = 80
check l3.restored_no_snap not_logged "^zfs snapshot"
# --dry-run on a clone: nothing remote, not even the read of the mount
setup $R105 "$A" - "$A"; export FAKE_CLONE=$CLONE
printf 'mp0: /%s,mp=/var/lib/docker/volumes/datacommune-public_pgdata/_data\n' "$CLONE" > "$SIM/ct-config"
run --dry-run "$B"
check l3.dry test "$RC" = 0
check l3.dry_no_ssh not_logged "^ssh "
check l3.dry_out grep -q "zfs snapshot <clone>@pre-$b12-<date>" <<<"$OUT"

# (l4) zfs unreadable for a clone mount: refused, never dumped
ran l4
setup $R105 "$A" - "$A"; export FAKE_CLONE=$CLONE FAKE_ZFS_FAIL=1
printf 'mp0: /%s,mp=/var/lib/docker/volumes/datacommune-public_pgdata/_data\n' "$CLONE" > "$SIM/ct-config"
run "$B"
check l4 test "$RC" = 80
check l4.no_dump not_logged "^dump"
check l4.no_snap not_logged "^zfs snapshot"
check l4.api api_is "running $a12 $A"

# (l2) dump held by the caller
ran l2
setup $R105 "$A" - "$A"; export FAKE_DB_SIZE=144000000000 FAKE_FREE=18000000000; run --backup-ref "$REF" "$B"
check l2 test "$RC" = 0
check l2.no_dump not_logged "^dump"
check l2.no_snap not_logged "^zfs snapshot"
check l2.ref test "$(cat "$PUBLIC_DIR"/backups/pre-"$b12"-*.ref)" = "$REF"

fi

if group usage; then
# (m) dump fails: partial file deleted, api never stopped
ran m
setup $R105 "$A" - "$A"; export FAKE_DUMP_FAIL=1; run "$B"
check m test "$RC" = 81
check m.out grep -q "partial file deleted" <<<"$OUT"
check m.no_file test -z "$(ls "$PUBLIC_DIR"/backups/)"
check m.no_stop not_logged "^stop "
setup $R105 "$A" - "$A"; export FAKE_DUMP_NOHDR=1; run "$B"
check m.hdr test "$RC" = 81
check m.hdr_no_file test -z "$(ls "$PUBLIC_DIR"/backups/)"
check m.hdr_no_stop not_logged "^stop "
setup $R105 "$A" - "$A"; mkdir -p "$PUBLIC_DIR/backups"; echo PGDMP-killed > "$PUBLIC_DIR/backups/pre-$a12-20261102T114342.pgdump.part"
run "$B"
check m.stale test "$RC" = 0
check m.stale_gone test -z "$(ls "$PUBLIC_DIR"/backups/*.part 2>/dev/null)"

# (n) usage
ran n
for bad in 'zfs:dcpool/work/pg@pub-1' 'file:relative.dump' 'file:/x;rm'; do
  setup $R105 "$A" - "$A"; run --backup-ref "$bad" "$B"
  check "n.bad $bad" test "$RC" = 2
  check "n.bad_no_build $bad" not_logged "^build"
done
for f in 0,5 0; do
  setup $R105 "$A" - "$A"; PUBLIC_DUMP_SIZE_FACTOR="$f" run "$B"
  check "n.factor $f" test "$RC" = 2
done
setup $R105 "$C" "$A" "$C"; run --backup-ref "$REF" --rollback
check n.rollback test "$RC" = 2
check n.no_up not_logged "^up "

fi

if group publication; then
# (o) only a commit of the public master is deployed (D-135, 05-07 lot 4)
ran o
git -C "$GIT_REPO" update-ref "$PUBLISHED" "$B"
setup $R105 "$A" - "$A"; run "$C"
check o.unpublished test "$RC" = 80
check o.unpublished_names grep -q "not on $PUBLISHED" <<<"$OUT"
check o.unpublished_nothing test ! -s "$FAKE_LOG"
setup $R105 "$A" - "$A"; run --dry-run "$C"
check o.dry_refused test "$RC" = 80
setup $R105 "$A" - "$A"; run "$B"
check o.published test "$RC" = 0
setup $R105 "$A" - "$A"; run --allow-unpublished "$C"
check o.allowed test "$RC" = 0
check o.allowed_warns grep -q "WARNING: $C is not on $PUBLISHED" <<<"$OUT"
check o.allowed_deployed grep -q "DEPLOYED $C" <<<"$OUT"
git -C "$GIT_REPO" update-ref -d "$PUBLISHED"
setup $R105 "$A" - "$A"; run "$B"
check o.no_ref test "$RC" = 80
check o.no_ref_names grep -q "$PUBLISHED is unknown" <<<"$OUT"
check o.no_ref_nothing test ! -s "$FAKE_LOG"
setup $R105 "$C" "$A" "$C"; run --rollback
check o.rollback_unguarded test "$RC" = 0
git -C "$GIT_REPO" update-ref "$PUBLISHED" "$C"

# (p) a protected container is never a target (PUBLIC_PROTECTED_CTIDS of the settings: 975)
ran p
setup $R105 "$A" - "$A"; PUBLIC_CTID=975 run "$B"
check p.protected test "$RC" = 2
check p.protected_names grep -q "ct/975 is protected" <<<"$OUT"
check p.protected_nothing test ! -s "$FAKE_LOG"

fi

echo "SCENARIOS ${RAN[*]}"
[ "${#RAN[@]}" -gt 0 ] || FAILED+=(no_scenario)
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "FAILED: ${FAILED[*]}"
  exit 1
fi
echo PUBLIC_DEPLOY_OK
