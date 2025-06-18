#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Intelligent Production Scheduling AI - one shot setup (Linux / macOS)
#
#   ./scripts/setup.sh            # install everything and seed the demo data
#   ./scripts/setup.sh --no-seed  # install only
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SEED=1
for arg in "$@"; do
  case "$arg" in
    --no-seed) SEED=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: $PYTHON not found - install Python 3.11 or newer" >&2
  exit 1
fi

echo "==> Creating the virtual environment"
"$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip

echo "==> Installing the backend dependencies"
pip install -r backend/requirements-dev.txt

echo "==> Installing the frontend dependencies"
(cd frontend && npm install)

if [ ! -f .env ]; then
  echo "==> Creating .env from .env.example"
  cp .env.example .env
fi

if [ "$SEED" -eq 1 ]; then
  echo "==> Seeding the demo factory (medium scale)"
  (cd backend && python -m app.cli reset --scale medium)
fi

cat <<'EOF'

Setup complete.

  Terminal 1:  source .venv/bin/activate && cd backend && uvicorn app.main:app --reload --port 8000
  Terminal 2:  cd frontend && npm run dev

Then open http://localhost:5173 and sign in with admin / admin.
EOF
