#!/bin/bash
# ForexMind auto-deploy (installed to /opt/forexmind/auto-deploy.sh by
# deploy/install.sh). Syncs the checkout to origin/main on every timer
# tick and restarts ONLY what changed. Deploy reports go to Telegram.
set -u
ROOT="${FXM_ROOT:-/opt/forexmind}"
cd "$ROOT" || exit 0
LOG="$ROOT/auto-deploy.log"
REMOTE_URL="${FXM_REMOTE:-https://__GITHUB_PAT__@github.com/cletusmawong-dev/forexmind-ai}"
TG_CHAT=8979013644

tg() {
  local tok=""
  [ -f deploy/vps/.env ] && tok="$(sed -n 's/^TELEGRAM_BOT_TOKEN=//p' deploy/vps/.env | head -n1 | tr -d '"')"
  [ -n "$tok" ] && curl -sm 8 "https://api.telegram.org/bot${tok}/sendMessage" \
      -d chat_id="$TG_CHAT" -d text="$1" >/dev/null 2>&1 || true
}

git fetch -q "$REMOTE_URL" main 2>/dev/null || exit 0
LOCAL="$(git rev-parse HEAD 2>/dev/null || echo none)"
REMOTE="$(git rev-parse FETCH_HEAD 2>/dev/null || echo none)"
[ "$LOCAL" = "$REMOTE" ] && exit 0
if [ "$LOCAL" = "none" ] || [ "$REMOTE" = "none" ]; then
  CHANGED="backend/ vps-bridge/ deploy/"       # broken HEAD: treat as everything
else
  CHANGED="$(git diff --name-only "$LOCAL" "$REMOTE")"
fi
git reset -q --hard "$REMOTE" || exit 0
[ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 1000000 ] && : > "$LOG"
echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') deploy ${REMOTE:0:7} (from ${LOCAL:0:7})" >> "$LOG"

PARTS=""
if echo "$CHANGED" | grep -q "^deploy/"; then
  bash deploy/install.sh >> "$LOG" 2>&1 || echo "install.sh FAILED" >> "$LOG"
  PARTS="${PARTS}installer "
fi
if echo "$CHANGED" | grep -q "^vps-bridge/"; then
  systemctl restart forexmind-bridge >> "$LOG" 2>&1 || true
  PARTS="${PARTS}bridge "
fi
if echo "$CHANGED" | grep -q "^backend/"; then
  (cd deploy/vps && bash setup.sh >> "$LOG" 2>&1) || echo "setup.sh FAILED" >> "$LOG"
  PARTS="${PARTS}backend "
fi

sleep 3
if curl -sm 8 http://localhost:8000/api/health 2>/dev/null | grep -q '"ok"'; then
  tg "ForexMind auto-deploy ${REMOTE:0:7} (${PARTS:-files}) - health OK"
else
  tg "WARNING: ForexMind auto-deploy ${REMOTE:0:7} (${PARTS:-files}) - health check FAILED, check the VPS"
fi
