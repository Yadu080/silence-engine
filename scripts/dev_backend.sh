#!/usr/bin/env bash
# Flask development server on 127.0.0.1:8000 (restart after Python changes).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
exec ../.venv/bin/python manage.py serve "$@"
