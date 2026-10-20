#!/usr/bin/env bash
# Provision (or update) an Azure App Service for Orbix and deploy this checkout.
#
# Usage:
#   scripts/deploy-azure.sh                         # interactive password prompt
#   ORBIX_DEPLOY_PASSWORD='...' scripts/deploy-azure.sh
#   APP_NAME=my-orbix scripts/deploy-azure.sh
#
# Requirements: azure-cli, logged in (`az login`), zip, python3.
# Safe to re-run: existing resources are updated, settings are overwritten with
# the values passed here, and only the app code is redeployed.
set -euo pipefail

APP_NAME="${APP_NAME:-}"
RESOURCE_GROUP="${RESOURCE_GROUP:-orbix-rg}"
LOCATION="${LOCATION:-centralindia}"
PLAN_NAME="${PLAN_NAME:-orbix-plan}"
SKU="${SKU:-B1}"
RUNTIME="${RUNTIME:-PYTHON:3.11}"
DATA_DIR="${DATA_DIR:-/home/orbix-data}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="$ROOT/.venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

log "Checking prerequisites"
command -v az >/dev/null || fail "Azure CLI (az) is not installed. See https://learn.microsoft.com/cli/azure/install-azure-cli"
command -v zip >/dev/null || fail "zip is required to package the app."
az account show >/dev/null 2>&1 || fail "Not signed in. Run: az login"
SUBSCRIPTION="$(az account show --query name -o tsv)"
echo "Subscription: $SUBSCRIPTION"

if [ -z "$APP_NAME" ]; then
  DEFAULT_APP="orbix-$(az account show --query user.name -o tsv | tr -cd '[:alnum:]' | cut -c1-12 | tr '[:upper:]' '[:lower:]')"
  read -r -p "App name [$DEFAULT_APP]: " APP_NAME
  APP_NAME="${APP_NAME:-$DEFAULT_APP}"
fi
case "$APP_NAME" in
  *[!a-z0-9-]*) fail "App name must be lowercase letters, digits, and hyphens." ;;
esac
echo "App name:    $APP_NAME"

if [ -z "${ORBIX_DEPLOY_PASSWORD:-}" ]; then
  read -r -s -p "Dashboard password (min 8 chars): " ORBIX_DEPLOY_PASSWORD
  echo
  read -r -s -p "Repeat password: " CONFIRM
  echo
  [ "$ORBIX_DEPLOY_PASSWORD" = "$CONFIRM" ] || fail "Passwords do not match."
fi
[ "${#ORBIX_DEPLOY_PASSWORD}" -ge 8 ] || fail "Use at least 8 characters."

log "Hashing the dashboard password"
PASSWORD_HASH="$(printf '%s' "$ORBIX_DEPLOY_PASSWORD" | "$PYTHON_BIN" -c \
  'import sys; from orbix.security import hash_password; print(hash_password(sys.stdin.read()))')"
unset ORBIX_DEPLOY_PASSWORD CONFIRM

if [ -n "${ORBIX_SESSION_SECRET:-}" ]; then
  SESSION_SECRET="$ORBIX_SESSION_SECRET"
else
  SESSION_SECRET="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(48))')"
fi

log "Ensuring resource group, plan, and web app"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
az appservice plan create --name "$PLAN_NAME" --resource-group "$RESOURCE_GROUP" \
  --sku "$SKU" --is-linux --output none
if az webapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Web app $APP_NAME already exists; updating it."
else
  az webapp create --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --plan "$PLAN_NAME" --runtime "$RUNTIME" --output none
fi

log "Applying startup command and settings"
# Exactly one worker: DuckDB and SQLite are single-process stores. /home is the
# only persistent path on App Service, so the data directory lives there.
az webapp config set --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
  --startup-file "bash startup.sh" --output none
az webapp config appsettings set --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
  --settings \
    WEBSITES_PORT=8000 \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    ORBIX_DATA_DIR="$DATA_DIR" \
    ORBIX_SESSION_SECRET="$SESSION_SECRET" \
    ORBIX_DASHBOARD_PASSWORD_HASH="$PASSWORD_HASH" \
    ORBIX_DASHBOARD_PASSWORD= \
  --output none

log "Packaging the app"
rm -f release.zip
zip -qr release.zip . \
  -x ".git/*" ".github/*" ".venv/*" "venv/*" "data/*" \
     "**/__pycache__/*" "*.py[cod]" ".pytest_cache/*" \
     ".env" "release.zip" "tests/*"

log "Deploying (this can take a few minutes)"
az webapp deployment source config-zip --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" --src release.zip --output none
rm -f release.zip

URL="https://$APP_NAME.azurewebsites.net"
log "Checking the deployment"
for attempt in $(seq 1 30); do
  if curl -fsS -m 10 "$URL/healthz" >/dev/null 2>&1; then
    echo "Live: $URL"
    echo "Login page: $URL/login"
    exit 0
  fi
  sleep 10
done

cat >&2 <<EOF

The app did not answer on /healthz within five minutes. Check:
  az webapp log tail --name $APP_NAME --resource-group $RESOURCE_GROUP
Common causes: the first Oryx build is still running, or the startup command
cannot find uvicorn (SCM_DO_BUILD_DURING_DEPLOYMENT must stay true).
EOF
exit 1
