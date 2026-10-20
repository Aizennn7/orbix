#!/usr/bin/env sh
# Start command shared by Docker, Azure App Service, and any PaaS that injects
# $PORT. Exactly one worker: DuckDB and SQLite are single-process stores.
set -eu

PORT="${PORT:-8000}"

# Prefer the interpreter on PATH (the container image, or the virtualenv Oryx
# builds on App Service). `python -m uvicorn` avoids depending on the console
# script being on PATH.
PYTHON="${PYTHON:-python}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python3

exec "$PYTHON" -m uvicorn orbix.api:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --workers 1 \
    --no-server-header
