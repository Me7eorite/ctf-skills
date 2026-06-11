#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT/web"
OUT_DIR="$WEB_DIR/out"
DIST_DIR="$ROOT/src/web/static/dist"

if ! command -v npm >/dev/null 2>&1; then
  echo "error: npm is required to build the frontend" >&2
  exit 127
fi

cd "$WEB_DIR"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
npm run build

rm -rf "$DIST_DIR"
mkdir -p "$(dirname "$DIST_DIR")"
cp -R "$OUT_DIR" "$DIST_DIR"

test -f "$DIST_DIR/index.html"
echo "built frontend at $DIST_DIR"
