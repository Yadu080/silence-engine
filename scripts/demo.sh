#!/usr/bin/env bash
# One-command local demo: installs if needed, generates the SYNTHETIC dataset once, serves on 127.0.0.1:8000.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -x .venv/bin/python ] || ./scripts/setup.sh
if [ ! -f frontend/dist/index.html ] && [ -f frontend/package.json ] && command -v npm >/dev/null 2>&1; then
  (cd frontend && npm ci --no-audit --no-fund && npm run build)
fi
.venv/bin/python backend/manage.py init-demo --if-empty
PORT="${SILENCE_PORT:-8000}"
echo ""
echo "  SILENCE ENGINE is starting — open http://127.0.0.1:${PORT}"
echo "  (synthetic demonstration data; press Ctrl+C to stop)"
echo ""
cd backend
if [ -x ../.venv/bin/gunicorn ]; then
  exec ../.venv/bin/gunicorn --config ../scripts/linuxone/gunicorn.conf.py wsgi:app
fi
exec ../.venv/bin/python manage.py serve
