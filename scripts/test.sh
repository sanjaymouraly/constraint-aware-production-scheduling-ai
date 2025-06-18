#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Run the complete quality gate (Linux / macOS).
#
#   ./scripts/test.sh            # everything except the slow benchmarks
#   ./scripts/test.sh --slow     # including the solver benchmarks
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

SLOW=0
if [ "${1:-}" = "--slow" ]; then SLOW=1; fi

echo "==> Backend lint"
(cd backend && ruff check app tests && ruff format --check app tests)

echo "==> Backend type check"
(cd backend && mypy app)

echo "==> Backend tests"
if [ "$SLOW" -eq 1 ]; then
  (cd backend && python -m pytest -q)
else
  (cd backend && python -m pytest -q -m "not slow")
fi

echo "==> Frontend lint"
(cd frontend && npm run lint)

echo "==> Frontend type check"
(cd frontend && npm run typecheck)

echo "==> Frontend component tests"
(cd frontend && npm run test)

echo "==> Frontend production build"
(cd frontend && npm run build)

echo ''
echo "All checks passed."
