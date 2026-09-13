#!/usr/bin/env bash
# Deploys the ForexMind AI frontend to Netlify using a personal access token.
#
# Usage:
#   export NETLIFY_TOKEN=nfp_xxxxxxxx            # required
#   export NETLIFY_SITE_NAME=forexmind-ai        # optional desired subdomain
#   export BACKEND_HOST=your-api.onrender.com    # optional (updates /api proxy)
#   ./scripts/netlify-deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

: "${NETLIFY_TOKEN:?Set NETLIFY_TOKEN (Netlify → User settings → Applications → Personal access tokens)}"

# 1) build + assemble
if [ -n "${BACKEND_HOST:-}" ]; then
  BACKEND_HOST="$BACKEND_HOST" ./scripts/prepare-deploy.sh
else
  ./scripts/prepare-deploy.sh
fi

# 2) create the site if we don't have its id yet
if [ -z "${NETLIFY_SITE_ID:-}" ]; then
  echo "==> Creating (or reusing) site '${NETLIFY_SITE_NAME:-forexmind-ai}'..."
  CREATE=$(curl -s -X POST https://api.netlify.com/api/v1/sites \
    -H "Authorization: Bearer ${NETLIFY_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"${NETLIFY_SITE_NAME:-forexmind-ai}\"}")
  SITE_ID=$(echo "$CREATE" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('id',''))" 2>/dev/null || true)
  if [ -z "$SITE_ID" ]; then
    # name may be taken -> list sites and reuse an exact-name match
    echo "==> Name taken or error; looking up existing site..."
    SITE_ID=$(curl -s "https://api.netlify.com/api/v1/sites?filter=all" \
      -H "Authorization: Bearer ${NETLIFY_TOKEN}" \
      | python3 -c "import sys,json;name='${NETLIFY_SITE_NAME:-forexmind-ai}';print(next((s['id'] for s in json.load(sys.stdin) if s.get('name')==name), ''))")
  fi
  [ -n "$SITE_ID" ] || { echo "Could not create or find the site. Response:"; echo "$CREATE" | head -c 400; exit 1; }
  echo "    site id: $SITE_ID"
fi

# 3) deploy the folder to production
echo "==> Deploying deploy-netlify/ to production..."
npx --yes netlify-cli@latest deploy \
  --dir=deploy-netlify \
  --prod \
  --auth="${NETLIFY_TOKEN}" \
  --site="${NETLIFY_SITE_ID:-}"

echo "==> Deployed. Your site URL is shown above (https://<name>.netlify.app)."
