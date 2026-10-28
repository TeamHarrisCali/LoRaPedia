#!/bin/sh
# Starts the app the way a user does, from docker-compose.yml, and checks it really comes up as intended.
#
#   scripts/compose_smoke.sh IMAGE [COMPOSE_FILE]     IMAGE: the app image to run (e.g. lorapedia-ci); COMPOSE_FILE: default docker-compose.yml
#
# Why this exists: the CI job that starts the image with `docker run` once passed while the compose file was broken (it named an
# entrypoint, which silently drops the image's command, so the container crash-looped). Only the compose file, as written, proves the
# compose file. Set SMOKE_PORT if 8000 is taken. Exit 0 = healthy, unprivileged, no capabilities.
set -eu

IMAGE="${1:?usage: compose_smoke.sh IMAGE [COMPOSE_FILE]}"
FILE="${2:-docker-compose.yml}"
PORT="${SMOKE_PORT:-8000}"
PROJECT="${SMOKE_PROJECT:-lpsmoke}"
DIR="$(mktemp -d)"

cp "$FILE" "$DIR/docker-compose.yml"
printf 'LORAPEDIA_IMAGE=%s\n' "$IMAGE" > "$DIR/.env"
{
    printf 'services:\n  lorapedia:\n    ports: !override ["127.0.0.1:%s:8000"]\n' "$PORT"
    # SMOKE_NO_RADIO=1: on a computer with a real radio plugged in, do not let this throwaway node pick it up and answer messages.
    [ -z "${SMOKE_NO_RADIO:-}" ] || printf '    environment:\n      LORAPEDIA_DEVICE_DIR: /nonexistent\n'
} > "$DIR/docker-compose.override.yml"
cd "$DIR"
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then docker compose -p "$PROJECT" logs --no-color --tail 40 lorapedia 2>&1 || true; fi
    docker compose -p "$PROJECT" down -v >/dev/null 2>&1 || true
    rm -rf "$DIR"
    exit "$status"
}
trap cleanup EXIT

docker compose -p "$PROJECT" up -d --no-deps lorapedia
cid="$(docker compose -p "$PROJECT" ps -q lorapedia)"
health=starting
for _ in $(seq 1 90); do
    health="$(docker inspect --format '{{.State.Health.Status}}' "$cid")"
    [ "$health" = healthy ] && break
    state="$(docker inspect --format '{{.State.Status}}' "$cid")"
    [ "$state" = running ] || { echo "container is $state (restarting or exited): it did not stay up"; exit 1; }
    sleep 2
done
echo "health: $health"
[ "$health" = healthy ] || exit 1

status="$(docker exec "$cid" cat /proc/1/status)"
field() { printf '%s\n' "$status" | awk -v k="$1:" '$1 == k { print $2 }'; }
echo "app process: uid=$(field Uid) capabilities=$(field CapEff) no_new_privs=$(field NoNewPrivs)"
[ "$(field Uid)" = 10001 ] || { echo "the app is not running as the unprivileged user"; exit 1; }
[ "$(field CapEff)" = 0000000000000000 ] || { echo "the app process still holds capabilities"; exit 1; }
[ "$(field NoNewPrivs)" = 1 ] || { echo "no-new-privs is not set"; exit 1; }
curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null
echo "compose smoke test passed"
