#!/usr/bin/env bash
# Orbix Codespace Post-Start - Quick health check
echo "==> Orbix Codespace Started - Root DB Check"
python scripts/init_db.py --check 2>&1 | tail -n 15
echo ""
echo "Ports: 8000 (web), 3000 (mobile PWA), 8001 (mobile API)"
echo "Run: uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1"
