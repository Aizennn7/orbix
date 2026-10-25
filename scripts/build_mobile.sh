#!/usr/bin/env bash
# Build the Orbix mobile PWA (Vite) into mobile/dist.
#
# dist/ is a build artifact and is not committed. Configuration comes from the
# root .env at runtime via /api/database/config - nothing is baked into the
# bundle, so no credentials can leak into a published artifact.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Building Orbix mobile PWA"

if [ ! -f "mobile/src/index.html" ]; then
  echo "ERROR: mobile/src/index.html is missing" >&2
  exit 1
fi

cd mobile

if [ ! -d node_modules ]; then
  echo "  Installing dependencies"
  npm install
fi

echo "  Running Vite build"
npm run build

cd "$ROOT"

# Static assets served alongside the PWA shell.
cp web/icon-*.png mobile/dist/ 2>/dev/null || true
cp web/manifest.json mobile/dist/ 2>/dev/null || true
cp web/service-worker.js mobile/dist/ 2>/dev/null || true

echo ""
echo "==> Built to mobile/dist/"
ls -lh mobile/dist/ | awk '{print "  ", $9, $5}' | head -n 20
