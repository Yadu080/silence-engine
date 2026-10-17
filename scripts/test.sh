#!/usr/bin/env bash
# Run every automated check: backend unit/API/leakage tests, then frontend type-check and production build.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
../.venv/bin/python -m pytest -q
if command -v npm >/dev/null 2>&1 && [ -d "$ROOT/frontend/node_modules" ]; then
  cd "$ROOT/frontend"
  npm run build
else
  echo "Skipping frontend checks (npm or node_modules not available)."
fi
