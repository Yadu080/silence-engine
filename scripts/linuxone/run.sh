#!/usr/bin/env bash
# Start the backend with gunicorn, bound to 127.0.0.1:8000 unless .env says otherwise.
#   ./scripts/linuxone/run.sh              foreground (Ctrl+C to stop)
#   ./scripts/linuxone/run.sh --background detached; logs in data/server.log, pid in data/server.pid
set -euo pipefail
if [ "$(id -u)" = "0" ] && [ "${SILENCE_ALLOW_ROOT:-0}" != "1" ]; then
  echo "Refusing to run as root (set SILENCE_ALLOW_ROOT=1 only inside a single-user container)." >&2; exit 1
fi
APP="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$APP/backend"
GUNICORN="$APP/.venv/bin/gunicorn"
[ -x "$GUNICORN" ] || { echo "Run scripts/linuxone/install.sh first." >&2; exit 1; }
CONF="$APP/scripts/linuxone/gunicorn.conf.py"
if [ "${1:-}" = "--background" ]; then
  if [ -f "$APP/data/server.pid" ] && kill -0 "$(cat "$APP/data/server.pid")" 2>/dev/null; then
    echo "Already running (pid $(cat "$APP/data/server.pid"))."; exit 0
  fi
  nohup "$GUNICORN" --config "$CONF" --pid "$APP/data/server.pid" wsgi:app >>"$APP/data/server.log" 2>&1 &
  sleep 2
  echo "Started (pid $(cat "$APP/data/server.pid" 2>/dev/null || echo '?')). Logs: $APP/data/server.log"
  curl -s --max-time 5 "http://127.0.0.1:${SILENCE_PORT:-8000}/api/health" || echo "(health check not ready yet — try again in a few seconds)"
  echo ""
else
  exec "$GUNICORN" --config "$CONF" wsgi:app
fi
