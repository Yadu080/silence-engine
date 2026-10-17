#!/usr/bin/env bash
# Vite dev server on http://127.0.0.1:5173 with hot reload; /api is proxied to the backend on :8000.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"
[ -d node_modules ] || npm ci --no-audit --no-fund
exec npm run dev
