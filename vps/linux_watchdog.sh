#!/usr/bin/env bash
# ForexMind Linux watchdog - ONE idempotent healing pass, run every minute by
# the forexmind-watchdog.timer. Stdlib-only bash + curl.
#
#   bridge down -> restart the service (systemd handles crash-restarts too;
#                  this catches hangs that stay "active" but silent)
#   MT5 terminal process missing -> start it via wine (uses the DETECTED
#                  existing installation; never reinstalls/reconfigures)
#   everything OK -> log one line and exit
#
# Env comes from /etc/forexmind-bridge.env (EnvironmentFile= in the unit).
set -uo pipefail
: "${BRIDGE_TOKEN:?}" "${BRIDGE_PORT:=8700}" "${WINEPREFIX:?}"
LOG="${LOG:-/var/log/forexmind-watchdog.log}"
TS() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(TS)] $*" >> "$LOG"; tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" || true; }

H=$(curl -sf -m 8 -H "X-Bridge-Token: $BRIDGE_TOKEN" "http://127.0.0.1:${BRIDGE_PORT}/health" 2>/dev/null)
if [[ -z "$H" ]]; then
  # is the terminal process alive under wine?
  if ! pgrep -f "terminal64.exe" >/dev/null 2>&1; then
    TERM_EXE="${MT5_TERMINAL_EXE:-}"
    if [[ -n "$TERM_EXE" ]]; then
      log "MT5 terminal not running - starting $TERM_EXE (existing installation)"
      (cd "$(dirname "$0")/.." && WINEPREFIX="$WINEPREFIX" DISPLAY="${DISPLAY:-:0}" \
        WINEDEBUG=-all nohup wine "$TERM_EXE" >/dev/null 2>&1 &)
      sleep 20
    else
      log "WARN terminal not running and MT5_TERMINAL_EXE unset"
    fi
  fi
  log "bridge DOWN - restarting forexmind-bridge"
  systemctl restart forexmind-bridge.service || log "ERROR restart failed"
  exit 0
fi

# bridge answers - report connection state honestly
CONNECTED=$(echo "$H" | grep -o '"connected":[a-z]*' | head -1 | cut -d: -f2)
TRADE_OK=$(echo "$H" | grep -o '"trade_allowed":[a-z]*' | head -1 | cut -d: -f2)
if [[ "$CONNECTED" != "true" ]]; then
  log "bridge up but MT5 terminal NOT connected to broker"
elif [[ "$TRADE_OK" != "true" ]]; then
  log "WARN MT5 trade_allowed=false - enable Algo Trading in the terminal"
else
  log "OK bridge up, MT5 connected"
fi
