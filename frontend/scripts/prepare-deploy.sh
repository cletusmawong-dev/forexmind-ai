#!/usr/bin/env bash
# Assembles a ready-to-drag-and-drop Netlify deploy folder.
# Usage:
#   1. BACKEND_HOST=forexmind-ai-api.onrender.com ./scripts/prepare-deploy.sh
#   2. Drag frontend/deploy-netlify onto https://app.netlify.com/drop
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND_HOST="${BACKEND_HOST:-REPLACE-WITH-YOUR-BACKEND-HOST}"

echo "==> Building production bundle..."
npm run build

echo "==> Assembling deploy-netlify/ (backend: ${BACKEND_HOST})"
rm -rf deploy-netlify
mkdir -p deploy-netlify
cp -r dist/* deploy-netlify/

# bake the backend host into the redirect rules (netlify.toml is intentionally
# NOT included in drag-and-drop deploys - the CLI would try to run a build).
# _redirects carries the /api proxy + SPA fallback; _headers carries security
# headers + manifest content type.
sed "s|REPLACE-WITH-YOUR-BACKEND-HOST|${BACKEND_HOST}|g" public/_redirects > deploy-netlify/_redirects
cat > deploy-netlify/_headers <<'HDR'
/manifest.webmanifest
  Content-Type: application/manifest+json

/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
HDR

echo "==> Done. Deploy contents:"
ls deploy-netlify
echo ""
echo "Next: drag the deploy-netlify folder onto https://app.netlify.com/drop"
