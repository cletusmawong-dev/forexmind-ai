#!/usr/bin/env bash
# Creates a GitHub repo and pushes ForexMind AI to it.
# Usage:
#   export GH_TOKEN=ghp_xxxxx            # classic token with "repo" scope
#   export GH_USER=cletusmawong          # your GitHub username
#   export GH_REPO=forexmind-ai          # repo name (created if missing)
#   export GH_PRIVATE=1                  # 1 = private repo (default)
#   bash scripts/push-to-github.sh
set -euo pipefail
cd "$(dirname "$0")/.."

: "${GH_TOKEN:?Set GH_TOKEN (GitHub personal access token, classic, repo scope)}"
GH_USER="${GH_USER:?Set GH_USER (your GitHub username)}"
GH_REPO="${GH_REPO:-forexmind-ai}"
GH_PRIVATE="${GH_PRIVATE:-1}"

echo "==> Verifying token..."
curl -s -H "Authorization: Bearer ${GH_TOKEN}" https://api.github.com/user \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('    authenticated as:', d.get('login', d))"

echo "==> Creating repo ${GH_USER}/${GH_REPO} (private=${GH_PRIVATE}) if missing..."
CREATE=$(curl -s -X POST https://api.github.com/user/repos \
  -H "Authorization: Bearer ${GH_TOKEN}" -H "Content-Type: application/json" \
  -d "{\"name\":\"${GH_REPO}\",\"private\":${GH_PRIVATE},\"description\":\"ForexMind AI - AI trading research & signal agent\"}")
echo "$CREATE" | python3 -c "import sys,json;d=json.load(sys.stdin);print('    ->', d.get('html_url') or d.get('errors') or d)" | head -2

if [ ! -d .git ]; then
  git init -q
  git config user.name "ForexMind Deploy"
  git config user.email "deploy@forexmind.local"
  git add -A
  git commit -qm "ForexMind AI: FastAPI agent backend + React liquid-glass PWA"
fi
git remote remove origin 2>/dev/null || true
git remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GH_USER}/${GH_REPO}.git"

echo "==> Pushing..."
BRANCH="main"
git branch -M "$BRANCH" 2>/dev/null || true
if git ls-remote --heads origin "$BRANCH" | grep -q "$BRANCH"; then
  git push -q origin "$BRANCH"
else
  git push -q -u origin "$BRANCH"
fi
echo "==> Pushed to https://github.com/${GH_USER}/${GH_REPO}"
