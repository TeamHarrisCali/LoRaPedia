#!/bin/sh
# Starts LoRaPedia inside its container.
#
# Why this exists: a USB radio shows up as a device file such as /dev/ttyACM0 whose GROUP differs from one computer to the next
# (dialout, uucp, plugdev, or a number nobody named), and a container's user can only open it if it belongs to that group. The
# compose file cannot know the number in advance, so it starts the container as root with only the two capabilities needed to
# change identity (SETUID, SETGID). This script reads the groups of the serial devices it can see, then immediately becomes the
# unprivileged user (uid 10001) with exactly those groups and every capability removed, and only then runs the app. The app
# itself never runs as root.
#
# Started as an ordinary user (an older compose file, `docker run`), there is nothing to change: it just runs the command.
# LORAPEDIA_ENTRYPOINT_DRY_RUN=1 prints what would be run instead of running it (used by the tests).
set -eu

APP_UID=10001
APP_GID=10001
DEV="${LORAPEDIA_DEVICE_DIR:-/dev}"

if [ "${LORAPEDIA_ENTRYPOINT_DRY_RUN:-}" != "1" ] && [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

gids=""
count=0
add_gid() {
    # `return 0`, never a bare `return`: that returns the status of the last test (false), which `set -e` would turn into an exit.
    case "$1" in ''|*[!0-9]*) return 0 ;; esac  # numbers only
    [ "$1" = "0" ] && return 0                  # never hand out root's group
    case ",$gids," in *",$1,"*) return 0 ;; esac
    [ "$count" -ge 16 ] && return 0
    gids="${gids:+$gids,}$1"
    count=$((count + 1))
}

# Every serial-port style device file (ttyACM0, ttyUSB0, ttyS0, ...), not the virtual consoles (tty0, tty1) and not "tty" itself.
for node in "$DEV"/tty[A-Za-z]*; do
    [ -c "$node" ] || continue
    add_gid "$(stat -L -c %g "$node" 2>/dev/null || true)"
done
# For a device that is not plugged in yet, or a group this cannot see: LORAPEDIA_EXTRA_GIDS="20,986"
IFS=','
for g in ${LORAPEDIA_EXTRA_GIDS:-}; do add_gid "$g"; done
unset IFS

if [ -n "$gids" ]; then
    set -- setpriv --reuid="$APP_UID" --regid="$APP_GID" --groups="$gids" --bounding-set=-all --inh-caps=-all --no-new-privs -- "$@"
else
    set -- setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups --bounding-set=-all --inh-caps=-all --no-new-privs -- "$@"
fi

if [ "${LORAPEDIA_ENTRYPOINT_DRY_RUN:-}" = "1" ]; then
    printf '%s\n' "$*"
    exit 0
fi
exec "$@"
