#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Start the FastAPI backend (Linux / macOS).
#
#   ./scripts/start-backend.sh              # http://127.0.0.1:8000
#   ./scripts/start-backend.sh --reload    # with auto reload
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -d .venv ]; then
  echo "error: .venv not found - run ./scripts/setup.sh first" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RELOAD=""
if [ "${1:-}" = "--reload" ]; then RELOAD="--reload"; fi

cd backend
exec uvicorn app.main:app --host "$HOST" --port "$PORT" $RELOAD
