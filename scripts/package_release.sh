#!/usr/bin/env bash
# Build the frontend on this machine and bundle everything LinuxONE needs into one tarball.
# The server then needs only Python 3.10+ (no Node.js).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="$(.venv/bin/python -c 'import sys; sys.path.insert(0, "backend"); import silence_engine; print(silence_engine.__version__)')"
NAME="silence-engine-${VERSION}"
OUT="$ROOT/release"
(cd frontend && npm run build)
rm -rf "$OUT/$NAME" && mkdir -p "$OUT/$NAME"
rsync -a --exclude '__pycache__' --exclude '.pytest_cache' backend "$OUT/$NAME/"
mkdir -p "$OUT/$NAME/frontend" && cp -R frontend/dist "$OUT/$NAME/frontend/dist"
cp -R scripts docs notebooks "$OUT/$NAME/"
cp README.md .env.example requirements*.txt "$OUT/$NAME/"
mkdir -p "$OUT/$NAME/data" && touch "$OUT/$NAME/data/.gitkeep"
# macOS Finder metadata is never part of the project.
find "$OUT/$NAME" -name .DS_Store -delete
# Safety check: ship only the project (code, docs, notebook, built dashboard); never keys, local data or stray folders.
LEAKS="$(find "$OUT/$NAME" \( -name 'submission' -o -name 'api-samples' -o -name '.env' -o -name '*.pem' -o -name '*.sqlite3*' \
  -o -name '*.zip' -o -name '.DS_Store' -o -name '.*' ! -name '.' ! -name '.env.example' ! -name '.gitkeep' \) -print)"
if [ -n "$LEAKS" ]; then
  echo "ERROR: refusing to build; these files must not ship:" >&2
  echo "$LEAKS" >&2
  rm -rf "${OUT:?}/$NAME"
  exit 1
fi
# Keep macOS metadata (._ files, xattrs) out of the archive so Linux tar extracts it cleanly.
if tar --version 2>/dev/null | grep -qi bsdtar; then
  COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -C "$OUT" -czf "$OUT/$NAME.tar.gz" "$NAME"
else
  tar -C "$OUT" -czf "$OUT/$NAME.tar.gz" "$NAME"
fi
rm -rf "${OUT:?}/$NAME"
if command -v shasum >/dev/null 2>&1; then SUM="$(shasum -a 256 "$OUT/$NAME.tar.gz")"; else SUM="$(sha256sum "$OUT/$NAME.tar.gz")"; fi
echo "Release: $OUT/$NAME.tar.gz"
echo "SHA-256: ${SUM%% *}"
