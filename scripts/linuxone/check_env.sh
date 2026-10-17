#!/usr/bin/env bash
# Read-only environment check for LinuxONE (s390x) or any Linux host. Changes nothing.
set -uo pipefail
echo "== SILENCE ENGINE environment check =="
echo "User:         $(id -un) (uid $(id -u))"
[ "$(id -u)" = "0" ] && echo "WARNING: running as root. Install and run the app as a normal user."
echo "Architecture: $(uname -m)"
if [ "$(uname -m)" = "s390x" ]; then echo "              s390x detected (IBM Z / LinuxONE instruction set)"; fi
if [ -r /etc/os-release ]; then . /etc/os-release; echo "OS:           ${PRETTY_NAME:-unknown}"; fi
if [ -r /proc/sysinfo ]; then
  echo "Machine (from /proc/sysinfo):"
  grep -E '^(Manufacturer|Type|Model):' /proc/sysinfo | head -3 | sed 's/^/              /'
fi
echo "Kernel:       $(uname -r)"
echo "CPUs:         $(getconf _NPROCESSORS_ONLN 2>/dev/null || echo '?')"
echo "Disk free:    $(df -h "$HOME" | awk 'NR==2 {print $4 " in " $6}')"
echo ""
echo "== Python interpreters (3.10+ required) =="
BEST=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v="$("$c" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"
    ok="$("$c" -c 'import sys; print("ok" if sys.version_info >= (3,10) else "too old")' 2>/dev/null)"
    echo "  $c -> $v ($ok)"
    if [ -z "$BEST" ] && [ "$ok" = "ok" ]; then BEST="$c"; fi
  fi
done
if [ -n "$BEST" ]; then
  echo "Recommended:  PYTHON=$BEST"
  "$BEST" - <<'PY'
import importlib, sqlite3, sys
print(f"  sqlite3 module: SQLite {sqlite3.sqlite_version}")
for mod in ("venv", "ensurepip", "ssl"):
    try:
        importlib.import_module(mod)
        print(f"  {mod}: available")
    except Exception as exc:  # noqa: BLE001
        print(f"  {mod}: MISSING ({exc.__class__.__name__}) — install your distro's python3-venv / python3.x package")
print(f"  byte order: {sys.byteorder}-endian")
PY
else
  echo "No Python 3.10+ found. Install one, for example:"
  echo "  RHEL 8/9:      sudo dnf install -y python3.11 python3.11-devel gcc"
  echo "  Ubuntu 22.04+: sudo apt-get install -y python3 python3-venv python3-dev build-essential"
  echo "  SLES 15:       sudo zypper install -y python311 python311-devel gcc"
fi
echo ""
echo "== Optional ML stack for the ML triage benchmark (read-only; PyPI has no s390x wheels for numpy/scikit-learn) =="
for c in python3 python3.13 python3.12 python3.11 python3.10; do
  command -v "$c" >/dev/null 2>&1 || continue
  "$c" - <<'PY' 2>/dev/null
import sys
from importlib import metadata
found = []
for name in ("scikit-learn", "numpy", "scipy", "pandas", "xgboost", "torch", "tensorflow"):
    try:
        found.append(f"{name} {metadata.version(name)}")
    except metadata.PackageNotFoundError:
        pass
print(f"  {sys.executable}: " + (", ".join(found) if found else "none installed (the ML section of the dashboard will say 'not available')"))
PY
done
echo ""
echo "== Optional build tools (only for MarkupSafe's C speedups; pure-Python fallback otherwise) =="
command -v gcc >/dev/null 2>&1 && echo "  gcc: $(gcc --version | head -1)" || echo "  gcc: not installed (fine — MarkupSafe falls back to pure Python)"
echo ""
echo "== Network =="
PORT="${SILENCE_PORT:-8000}"
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
  echo "  Port ${PORT} is already in use."
else
  echo "  Port ${PORT} looks free."
fi
command -v curl >/dev/null 2>&1 && (curl -s -o /dev/null -w "  PyPI reachable: HTTP %{http_code}\n" --max-time 8 https://pypi.org/simple/flask/ || echo "  PyPI not reachable — use an offline wheelhouse (see docs/IBM_Z_DEPLOYMENT.md)")
