#!/usr/bin/env bash
# Stop a backend started with run.sh --background.
set -euo pipefail
APP="$(cd "$(dirname "$0")/../.." && pwd)"
PID_FILE="$APP/data/server.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  kill "$(cat "$PID_FILE")" && echo "Stopped pid $(cat "$PID_FILE")."
  rm -f "$PID_FILE"
else
  echo "Not running."
fi
