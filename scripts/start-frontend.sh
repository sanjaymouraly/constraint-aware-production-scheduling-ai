#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Start the Vite development server (Linux / macOS).
#
#   ./scripts/start-frontend.sh
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"

if [ ! -d node_modules ]; then
  echo "error: node_modules not found - run ./scripts/setup.sh first" >&2
  exit 1
fi

export VITE_API_TARGET="${VITE_API_TARGET:-http://127.0.0.1:8000}"
exec npm run dev -- --host "${HOST:-0.0.0.0}" --port "${PORT:-5173}"
