#!/usr/bin/env bash
# Creates the ForexMind AI backend service on Render via the API and waits
# for the first live deploy.
# Usage:
#   export RENDER_TOKEN=rnd_xxxxx
#   export GH_USER=cletusmawong
#   export GH_REPO=forexmind-ai
#   bash scripts/render-deploy.sh
set -euo pipefail

: "${RENDER_TOKEN:?Set RENDER_TOKEN (dashboard.render.com -> Account Settings -> API Keys)}"
GH_USER="${GH_USER:?Set GH_USER}"
GH_REPO="${GH_REPO:-forexmind-ai}"

API="https://api.render.com/v1"
AUTH="Authorization: Bearer ${RENDER_TOKEN}"

echo "==> Verifying token / finding workspace..."
OWNER_ID=$(curl -s -H "$AUTH" "$API/owners?limit=1" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['owners'][0]['id'] if d.get('owners') else '')")
[ -n "$OWNER_ID" ] || { echo "Could not read Render workspace (token invalid?)"; exit 1; }
echo "    owner: $OWNER_ID"

echo "==> Checking for existing service..."
EXISTING=$(curl -s -H "$AUTH" "$API/services?limit=20" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(next((s['service']['id'] for s in d.get('services', []) if s['service'].get('name') == 'forexmind-ai-api'), '')")
if [ -n "$EXISTING" ]; then
  SVC_ID="$EXISTING"
  echo "    found existing: $SVC_ID"
else
  echo "==> Creating web service 'forexmind-ai-api' (Docker, free)..."
  CREATE=$(curl -s -X POST "$API/services" -H "$AUTH" -H "Content-Type: application/json" -d "{
    \"type\": \"web_service\",
    \"name\": \"forexmind-ai-api\",
    \"ownerId\": \"$OWNER_ID\",
    \"repo\": \"https://github.com/${GH_USER}/${GH_REPO}\",
    \"branch\": \"main\",
    \"runtime\": \"docker\",
    \"dockerfilePath\": \"backend/Dockerfile\",
    \"dockerContext\": \"backend/\",
    \"planId\": \"free\",
    \"region\": \"oregon\",
    \"healthCheckPath\": \"/api/health\",
    \"autoDeploy\": \"yes\",
    \"envVars\": [{\"key\": \"REPLAY_ENABLED\", \"value\": \"1\"}]
  }")
  SVC_ID=$(echo "$CREATE" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('id','') if isinstance(d,dict) else '')")
  if [ -z "$SVC_ID" ]; then
    echo "SERVICE CREATION FAILED:"; echo "$CREATE" | head -c 600; exit 1
  fi
  echo "    service: $SVC_ID"
fi

echo "==> Triggering deploy..."
DEPLOY=$(curl -s -X POST "$API/services/${SVC_ID}/deploys" -H "$AUTH" -H "Content-Type: application/json" -d '{"clearCache": "clear"}')
DEPLOY_ID=$(echo "$DEPLOY" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('id',''))")
echo "    deploy: $DEPLOY_ID"

echo "==> Waiting for the deploy to go live (usually 3-6 minutes)..."
for i in $(seq 1 60); do
  sleep 12
  STATUS=$(curl -s -H "$AUTH" "$API/services/${SVC_ID}/deploys/${DEPLOY_ID}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))")
  echo "    [$i] $STATUS"
  case "$STATUS" in
    live) break ;;
    build_failed|update_failed|deactivated|canceled)
      echo "DEPLOY FAILED: $STATUS — check https://dashboard.render.com"; exit 1 ;;
  esac
done

URL=$(curl -s -H "$AUTH" "$API/services/${SVC_ID}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('serviceUrl',''))")
echo ""
echo "==> Backend URL: $URL"
echo "==> Health check: curl $URL/api/health"
