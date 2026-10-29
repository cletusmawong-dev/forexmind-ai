#!/bin/bash
# One-time installer for the ForexMind auto-deploy poller.
#   cd /opt/forexmind && GITHUB_PAT=ghp_xxx bash deploy/install.sh --first-run
# Idempotent: safe to re-run. The auto-deployer itself re-runs this whenever
# deploy/ changes - so the GitHub token is preserved from the previously
# installed script when GITHUB_PAT is not provided, and the script file is
# replaced ATOMICALLY (mv) so a running deploy tick is never corrupted.
set -e
ROOT="${FXM_ROOT:-/opt/forexmind}"
UNIT_DIR="${FXM_UNIT_DIR:-/etc/systemd/system}"
cd "$ROOT"

NEW="$ROOT/auto-deploy.sh.new"
cp deploy/auto-deploy.sh "$NEW"
if [ -n "${GITHUB_PAT:-}" ]; then
  sed -i "s|__GITHUB_PAT__|${GITHUB_PAT}|g" "$NEW"
elif [ -f "$ROOT/auto-deploy.sh" ]; then
  TOK="$(sed -n 's|^REMOTE_URL=.*https://\([^@"]*\)@github.com.*|\1|p' "$ROOT/auto-deploy.sh" | head -n1)"
  if [ -n "$TOK" ]; then
    sed -i "s|__GITHUB_PAT__|${TOK}|g" "$NEW"
  fi
fi
if grep -q "__GITHUB_PAT__" "$NEW"; then
  rm -f "$NEW"
  echo "ERROR: no GitHub token available (pass GITHUB_PAT on first install)." >&2
  echo "Run:   cd /opt/forexmind && GITHUB_PAT=ghp_... bash deploy/install.sh --first-run" >&2
  exit 1
fi
chmod 700 "$NEW"
mv -f "$NEW" "$ROOT/auto-deploy.sh"

cp deploy/fxm-deploy.service deploy/fxm-deploy.timer "$UNIT_DIR/"
systemctl daemon-reload
systemctl enable --now fxm-deploy.timer

if [ "${1:-}" = "--first-run" ]; then
  echo "== first deploy: rebuilding backend =="
  if [ -f deploy/vps/setup.sh ]; then (cd deploy/vps && bash setup.sh); fi
fi

echo "auto-deploy installed:"
systemctl list-timers --no-pager 2>/dev/null | grep fxm-deploy || true
