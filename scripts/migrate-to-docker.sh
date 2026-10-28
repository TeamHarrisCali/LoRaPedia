#!/usr/bin/env bash
# Copy the data folder of a ./run.sh install into the Docker volume the compose file uses, and fix up what would break there.
#
#   scripts/migrate-to-docker.sh                       # ./data -> the volume for this folder's compose project
#   scripts/migrate-to-docker.sh --project lptrial     # a trial copy under another compose project name
#
# It COPIES: the source folder is mounted read-only and never changed, so going back is just `./run.sh` again.
# It refuses if a LoRaPedia server is still answering on the source's port (stop it first: the copy must be consistent) or if the
# target volume already holds data. Then it repoints the library's file paths and any saved "localhost" Ollama address
# (scripts/migrate_data.py), and gives the files to the container's user (uid 10001).
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="./data"
PROJECT="$(basename "$PWD" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9_-')"   # compose's default project name for this folder
VOLUME=""
OLLAMA_URL="http://ollama:11434"
PORT=8000
# The same pinned image the app is built on, so the step needs nothing new to be downloaded or trusted.
IMAGE="python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70"

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SRC="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --volume) VOLUME="$2"; shift 2 ;;
    --ollama-url) OLLAMA_URL="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 2 ;;
  esac
done
VOLUME="${VOLUME:-${PROJECT}_lorapedia-data}"

command -v docker >/dev/null || { echo "Docker is not installed or not on PATH." >&2; exit 1; }
[ -f "$SRC/index.db" ] && [ -f "$SRC/lorapedia.db" ] || { echo "$SRC is not a LoRaPedia data folder (no index.db / lorapedia.db)." >&2; exit 1; }
SRC="$(cd "$SRC" && pwd)"

if curl -fsS -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "A LoRaPedia server is still answering on port ${PORT}. Stop it first so the copy is consistent." >&2
  exit 1
fi
if docker volume inspect "$VOLUME" >/dev/null 2>&1; then
  if [ -n "$(docker run --rm -v "$VOLUME":/data "$IMAGE" ls -A /data)" ]; then
    echo "Volume $VOLUME already has data in it; refusing to overwrite it." >&2
    exit 1
  fi
else
  # The labels make Docker Compose treat this as its own volume instead of warning about a foreign one.
  docker volume create --label "com.docker.compose.project=${PROJECT}" --label "com.docker.compose.volume=lorapedia-data" "$VOLUME" >/dev/null
fi

echo "Copying $SRC ($(du -sh "$SRC" | cut -f1)) into volume $VOLUME ..."
docker run --rm -v "$SRC":/src:ro -v "$VOLUME":/data "$IMAGE" cp -a /src/. /data/

echo "Fixing what refers to the old machine ..."
docker run --rm -v "$VOLUME":/data -v "$PWD/scripts/migrate_data.py":/migrate_data.py:ro "$IMAGE" \
  python /migrate_data.py /data --container-data /data --ollama-url "$OLLAMA_URL"

docker run --rm -v "$VOLUME":/data "$IMAGE" chown -R 10001:10001 /data
echo
echo "Done. $SRC was not changed."
echo "Start it with:   docker compose -p ${PROJECT} up -d"
