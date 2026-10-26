#!/usr/bin/env bash
# Start LoRaPedia without Docker. Safe to re-run: it only installs/builds what is missing.
#   ./run.sh                 # listen on this computer only (http://localhost:8000)
#   HOST=0.0.0.0 ./run.sh    # let other devices on your network open the UI
set -euo pipefail
cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
export LORAPEDIA_DATA="${LORAPEDIA_DATA:-$PWD/data}"

PY="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "LoRaPedia needs Python 3.11 or newer. Install it, then run ./run.sh again." >&2
  exit 1
fi

# 1. Python environment (one-time)
if [ ! -x backend/.venv/bin/python ]; then
  echo "==> Creating Python environment (first run only; needs internet)"
  if command -v uv >/dev/null; then uv venv -q backend/.venv --python "$PY"; else "$PY" -m venv backend/.venv; fi
fi
if ! backend/.venv/bin/python -c 'import lorapedia' 2>/dev/null; then
  echo "==> Installing dependencies (first run only; needs internet)"
  if command -v uv >/dev/null; then uv pip install -q --python backend/.venv/bin/python -e backend
  else backend/.venv/bin/python -m pip install -q -e backend; fi
fi

# 2. Web UI (one-time). Skipped if already built.
if [ ! -f frontend/dist/index.html ]; then
  if command -v npm >/dev/null; then
    echo "==> Building the web UI (first run only; needs internet)"
    (cd frontend && npm ci --silent && npm run build --silent)
  else
    echo "Note: the web UI is not built and npm is not installed, so only the API will run." >&2
    echo "      Install Node.js 20+ and re-run, or use Docker (docker compose up)." >&2
  fi
fi

echo "==> LoRaPedia is starting at http://${HOST/0.0.0.0/localhost}:${PORT}  (data: $LORAPEDIA_DATA)"
exec backend/.venv/bin/python -m lorapedia --host "$HOST" --port "$PORT"
