#!/usr/bin/env bash
# Install SILENCE ENGINE from an unpacked release as a NON-ROOT user.
# Usage (from the release directory):  PYTHON=python3.11 ./scripts/linuxone/install.sh [--skip-tests]
# Optional ML triage: on LinuxONE, numpy/scikit-learn come from the ML container image's Python, not from PyPI (no s390x
# wheels). Run with SILENCE_VENV_SYSTEM_SITE=1 and PYTHON=<that Python> so the new venv can see them. Without it the
# dashboard's ML section simply reports "not available"; everything else is unchanged.
set -euo pipefail
if [ "$(id -u)" = "0" ] && [ "${SILENCE_ALLOW_ROOT:-0}" != "1" ]; then
  echo "Refusing to install as root. Log in as a normal user (e.g. linux1) and re-run." >&2
  echo "(Inside a single-user container such as a Jupyter image, set SILENCE_ALLOW_ROOT=1.)" >&2
  exit 1
fi
APP="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$APP"
SKIP_TESTS=0
[ "${1:-}" = "--skip-tests" ] && SKIP_TESTS=1

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then PY="$c"; break; fi
  done
fi
[ -n "$PY" ] || { echo "Python 3.10+ not found. Run scripts/linuxone/check_env.sh for install hints." >&2; exit 1; }
echo "== Using $PY ($("$PY" --version 2>&1)) on $(uname -m)"

SITE_FLAG=""
if [ "${SILENCE_VENV_SYSTEM_SITE:-0}" = "1" ]; then
  SITE_FLAG="--system-site-packages"
  echo "== The venv will also see $PY's installed packages (for the optional ML triage benchmark)"
fi
[ -d .venv ] || "$PY" -m venv $SITE_FLAG .venv
.venv/bin/python -m pip install --upgrade pip
# Exact tested versions first; fall back to the compatible ranges if a pin is unavailable.
if ! .venv/bin/python -m pip install -r requirements-lock.txt; then
  echo "Pinned install failed; installing compatible ranges instead."
  .venv/bin/python -m pip install -r requirements.txt -r requirements-deploy.txt -r requirements-dev.txt
fi
.venv/bin/python -c 'import importlib.util as u; print("MarkupSafe C speedups:", "compiled" if u.find_spec("markupsafe._speedups") else "not compiled (pure-Python fallback, fully functional)")'

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env from .env.example (edit it to set SILENCE_DEPLOYMENT_LABEL or SILENCE_API_TOKEN)."
fi
mkdir -p data && chmod 700 data

if [ "$SKIP_TESTS" = "0" ]; then
  echo "== Running the automated test suite on this machine"
  (cd backend && ../.venv/bin/python -m pytest -q)
fi

echo "== Generating the synthetic demo dataset (if the database is empty)"
.venv/bin/python backend/manage.py init-demo --if-empty
echo "== Reproducibility fingerprints (compare with the same command on your Mac)"
.venv/bin/python backend/manage.py fingerprint
echo ""
echo "Installed. Start with:  ./scripts/linuxone/run.sh --background"
