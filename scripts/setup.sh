#!/usr/bin/env bash
# One-time local setup (macOS or Linux): Python virtualenv + dependencies, then the frontend build.
# Usage: ./scripts/setup.sh            (PYTHON=python3.11 ./scripts/setup.sh to pick an interpreter)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"

"$PY" - <<'PYCHECK'
import sys
if sys.version_info < (3, 10):
    sys.exit(f"Python 3.10+ is required (found {sys.version.split()[0]}). Set PYTHON=/path/to/python3.x")
print(f"Using Python {sys.version.split()[0]}")
PYCHECK

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt -r requirements-deploy.txt -r requirements-dev.txt

if [ -f frontend/package.json ] && command -v npm >/dev/null 2>&1; then
  (cd frontend && npm ci --no-audit --no-fund && npm run build)
elif [ -f frontend/dist/index.html ]; then
  echo "Using the prebuilt dashboard in frontend/dist."
else
  echo "No frontend source or build found: install Node.js and run 'npm ci && npm run build' in frontend/."
fi
echo "Setup complete. Start the demo with: ./scripts/demo.sh"
