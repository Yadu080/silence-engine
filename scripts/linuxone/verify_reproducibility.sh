#!/usr/bin/env bash
# Compare this machine's fingerprints with values recorded on another machine (e.g. your Mac).
# Usage: ./scripts/linuxone/verify_reproducibility.sh <raw_synthetic_fingerprint> <evaluation_results_sha256>
set -euo pipefail
APP="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$("$APP/.venv/bin/python" "$APP/backend/manage.py" fingerprint)"
echo "$OUT"
RAW="$(echo "$OUT" | "$APP/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["raw_synthetic_fingerprint"])')"
RES="$(echo "$OUT" | "$APP/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["evaluation_results_sha256"])')"
status=0
[ "$RAW" = "${1:-}" ] && echo "Dataset fingerprint: MATCH" || { echo "Dataset fingerprint: DIFFERENT"; status=1; }
[ "$RES" = "${2:-}" ] && echo "Evaluation results:  MATCH" || { echo "Evaluation results:  DIFFERENT"; status=1; }
exit $status
