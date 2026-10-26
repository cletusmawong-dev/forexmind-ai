#!/usr/bin/env bash
# ForexMind AI backend - one-shot VPS setup (owner decision 2026-10-25).
# Idempotent: safe to re-run. Never touches the MT5 bridge on :8700.
set -euo pipefail
cd "$(dirname "$0")"

echo "== ForexMind AI backend - VPS setup =="

# 0) docker present?
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed on this box."
  echo "Install it first:  curl -fsSL https://get.docker.com | sh"
  exit 1
fi

# 1) .env present?
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from the template."
  echo ">> EDIT IT NOW:  nano .env   (fill every <...> from Render's Environment tab)"
  echo ">> then re-run:  bash setup.sh"
  exit 0
fi
if grep -q "<paste from Render" .env; then
  echo "ERROR: .env still has placeholders.  Edit it:  nano .env"
  exit 1
fi

# 2) bridge reachable (must NOT be touched, just checked)
echo "-- checking the MT5 bridge on :8700 ..."
BRIDGE=$(curl -s -o /dev/null -w "%{http_code}" -m 5 \
  -H "X-Bridge-Token: 20260914" http://169.58.98.139:8700/health || true)
echo "   bridge /health -> ${BRIDGE:-no-answer} (200 = perfect; other = we continue, bridge stays untouched)"

# 3) build + start
echo "-- building + starting (first build takes a few minutes) ..."
docker compose up -d --build 2>/dev/null || docker-compose up -d --build

# 4) wait for health
echo "-- waiting for /api/health ..."
for i in $(seq 1 30); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -m 5 http://127.0.0.1:8000/api/health || true)
  [ "$CODE" = "200" ] && break
  sleep 5
done
if [ "$CODE" != "200" ]; then
  echo "ERROR: health check failed after 150s. Logs:"
  docker compose logs --tail 40 2>/dev/null || docker-compose logs --tail 40
  exit 1
fi
echo "   healthy (200)."

# 5) public reachability (firewall hint)
PUB=$(curl -s -o /dev/null -w "%{http_code}" -m 8 http://169.58.98.139:8000/api/health || true)
if [ "$PUB" != "200" ]; then
  echo ""
  echo "!! Local health is OK but the public IP did not answer ($PUB)."
  echo "!! Open port 8000 in the firewall, e.g.:  sudo ufw allow 8000/tcp"
  echo "!! (and check the provider's firewall/console for port rules),"
  echo "!! then re-run this script."
fi

echo ""
echo "== DONE - backend is up on port 8000 =="
echo "Next: tell the assistant the URL  http://169.58.98.139:8000  so the app"
echo "proxy + Telegram webhook can be switched and verified."
