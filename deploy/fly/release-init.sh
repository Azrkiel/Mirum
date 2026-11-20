#!/bin/sh
# Fly Machines entrypoint for the RC1 release backend (backend/Dockerfile target "fly").
#
# The release startup guards (backend/startup_checks.py) want what compose.release.yml gets from
# docker options: an exact-mountpoint engineering dir owned by uid 10001, tmpfs scratch, a
# read-only root and a non-root process. Fly Machines have no such options, so this script builds
# the same layout at boot while still root, then drops to uid/gid 10001 with no capabilities.
# Fails closed: any error aborts the boot (set -eu); nothing is ever retried or skipped.
# Never prints the environment.
set -eu

APP_UID=10001
APP_GID=10001
VOLUME=/data
SRC="$VOLUME/engineering"
DST=/tmp/mirum/engineering

fail() {
    echo "MIRUM_FLY_INIT_REFUSED: $*" >&2
    exit 1
}

[ "$(id -u)" = "0" ] || fail "must run as root"

# 1. The Fly volume must really be mounted at /data (never write the data dir to the root disk).
awk '$5 == "/data" { found = 1 } END { exit !found }' /proc/self/mountinfo \
    || fail "$VOLUME is not a mountpoint (is the Fly volume mounted?)"

# 2. Writable scratch on tmpfs, then a private tmpfs for /tmp/mirum owned by the app user.
mount -t tmpfs -o size=256M,mode=1777,nosuid,nodev tmpfs /tmp
mkdir /tmp/mirum
mount -t tmpfs -o "size=256M,uid=$APP_UID,gid=$APP_GID,mode=0700,nosuid,nodev" tmpfs /tmp/mirum

# 3. Engineering data dir on the volume: owner 10001:10001, mode 0700, non-recursive, never a symlink.
#    The volume root must be root-owned (the app user can never plant a symlink there), and the
#    directory is re-checked right before chown/chmod; chown -h never follows a link.
[ "$(stat -c %u "$VOLUME")" = "0" ] || fail "$VOLUME is not owned by root"
[ ! -L "$SRC" ] || fail "$SRC is a symlink"
[ -e "$SRC" ] || mkdir "$SRC"
[ -d "$SRC" ] && [ ! -L "$SRC" ] || fail "$SRC is not a real directory"
chown -h "$APP_UID:$APP_GID" "$SRC"
chmod 0700 "$SRC"

# 4. Bind the volume directory onto the exact mountpoint the guards require, then confirm the
#    mountpoint has the owner and mode the guards will check.
mkdir "$DST"
mount --bind "$SRC" "$DST"
[ "$(stat -c %u:%g:%a "$DST")" = "$APP_UID:$APP_GID:700" ] || fail "$DST has the wrong owner or mode"

# 5. Read-only root (code is already root-owned and read-only to the app user).
mount -o remount,ro /

# 6. Run the image CMD as the app user: no supplementary groups, no capabilities, no privilege gain.
cd /tmp
exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups \
    --inh-caps=-all --bounding-set=-all --no-new-privs -- "$@"
