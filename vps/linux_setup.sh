#!/usr/bin/env bash
# =============================================================================
# ForexMind bridge setup for an UBUNTU VPS where MT5 ALREADY RUNS UNDER WINE.
#
# WHAT THIS SCRIPT DOES (and does NOT do):
#   DOES:  detect your EXISTING Wine prefix + MT5 - preferring the RUNNING
#          MT5 process itself - and attach to it; add Windows Python INSIDE
#          the same prefix if missing (required: the MetaTrader5 pip package
#          is Windows-only); install a systemd service (auto-start) + a
#          1-minute watchdog timer; open the bridge port in ufw if active.
#   NEVER: installs/reinstalls MetaTrader 5, changes Wine configuration,
#          touches your MT5 account/login, or edits the MT5 installation.
#
# Usage (from the repo root on the VPS):
#   sudo bash vps/linux_setup.sh --token 'your-long-secret'
#   sudo bash vps/linux_setup.sh --token '...' --port 8700 --prefix /path/.wine
# =============================================================================
set -euo pipefail

TOKEN="" ; PORT="8700" ; PREFIX_ARG="" ; DISPLAY_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token)   TOKEN="$2"; shift 2 ;;
    --port)    PORT="$2"; shift 2 ;;
    --prefix)  PREFIX_ARG="$2"; shift 2 ;;
    --display) DISPLAY_ARG="$2"; shift 2 ;;
    *) echo "unknown arg $1"; exit 1 ;;
  esac
done
if [[ -z "$TOKEN" || ${#TOKEN} -lt 16 ]]; then
  echo "ERROR: pass a strong token:  --token '16+ chars random secret'"; exit 1
fi
if [[ $EUID -ne 0 ]]; then echo "ERROR: run with sudo (systemd + firewall need root)."; exit 1; fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGE_DIR="$REPO_DIR/vps-bridge"
[[ -f "$BRIDGE_DIR/bridge.py" ]] || { echo "ERROR: bridge.py not found at $BRIDGE_DIR"; exit 1; }

echo "==> [1/7] Detecting your EXISTING Wine prefix + MT5 (nothing is modified)"

declare -a CANDS=()      # candidate wine prefixes, best first
declare -A SEEN=()
add_cand() {
  # NB: must ALWAYS return 0 - under `set -e` a non-zero return from this
  # function silently kills the whole script when a candidate is missing.
  if [[ -n "$1" && -d "$1/drive_c" ]] && [[ -z "${SEEN[$1]:-}" ]]; then
    SEEN[$1]=1
    CANDS+=("$1")
  fi
  return 0
}

[[ -n "$PREFIX_ARG" ]] && add_cand "$PREFIX_ARG"
[[ -n "${WINEPREFIX:-}" ]] && add_cand "$WINEPREFIX"

# a) the RUNNING MT5 wins: read WINEPREFIX + DISPLAY from its process env
DETECTED_DISPLAY=""
for pid in $(pgrep -f 'terminal64\.exe' 2>/dev/null || true); do
  env_file="/proc/$pid/environ"
  [[ -r "$env_file" ]] || continue
  p="$(tr '\0' '\n' < "$env_file" 2>/dev/null | grep '^WINEPREFIX=' | cut -d= -f2- || true)"
  d="$(tr '\0' '\n' < "$env_file" 2>/dev/null | grep '^DISPLAY='  | cut -d= -f2- || true)"
  [[ -n "$d" && -z "$DETECTED_DISPLAY" ]] && DETECTED_DISPLAY="$d"
  add_cand "$p"
done
# also any running wineserver (covers prefixes of running wine apps)
for pid in $(pgrep -x wineserver 2>/dev/null || true); do
  env_file="/proc/$pid/environ"
  [[ -r "$env_file" ]] || continue
  p="$(tr '\0' '\n' < "$env_file" 2>/dev/null | grep '^WINEPREFIX=' | cut -d= -f2- || true)"
  add_cand "$p"
done

# b) common locations
for d in "$HOME"/.wine "$HOME"/.wine-* /root/.wine /home/*/.wine /home/*/.wine-* \
         /opt/.wine* /opt/*/.wine* /srv/.wine* /srv/*/.wine*; do
  [[ -d "$d" ]] && add_cand "$d"
done

# c) full-disk sweep for terminal64.exe under any drive_c (covers custom paths)
while IFS= read -r hit; do
  [[ -n "$hit" ]] || continue
  add_cand "${hit%%/drive_c/*}"
done < <(find / -xdev -maxdepth 9 -path '*/drive_c/*' -name 'terminal64.exe' \
             -not -path '*/system32/*' 2>/dev/null | head -20)

((${#CANDS[@]})) || { echo "ERROR: no Wine prefix with drive_c found anywhere."; exit 1; }

# pick the first candidate that actually contains MT5
TERMINAL="" ; PREFIX=""
for p in "${CANDS[@]}"; do
  hit="$(find "$p/drive_c" -maxdepth 6 -iname 'terminal64.exe' -not -path '*/system32/*' 2>/dev/null | head -1 || true)"
  if [[ -n "$hit" ]]; then TERMINAL="$hit"; PREFIX="$p"; break; fi
done
[[ -n "$TERMINAL" ]] || {
  echo "ERROR: Wine prefix(es) found (${CANDS[*]}) but none contains terminal64.exe."
  echo "       If MT5 lives elsewhere: rerun with --prefix /path/to/prefix"
  exit 1
}
if [[ -n "$DISPLAY_ARG" ]]; then DISPLAY_VAL="$DISPLAY_ARG"; else DISPLAY_VAL="${DETECTED_DISPLAY:-${DISPLAY:-:0}}"; fi
OWNER="$(stat -c '%U' "$PREFIX" 2>/dev/null || echo root)"
echo "    Wine prefix  : $PREFIX  (owner: $OWNER)"
echo "    MT5 terminal : $TERMINAL"
echo "    DISPLAY      : $DISPLAY_VAL"
echo "    (detected only - MT5, Wine and your account are left untouched)"

echo "==> [2/7] Ensuring Windows Python inside the SAME prefix (additive only)"
PYLIN=""
for cand in Python312 Python311 Python310 Python39; do
  if [[ -x "$PREFIX/drive_c/$cand/python.exe" ]]; then PYLIN="$PREFIX/drive_c/$cand/python.exe"; break; fi
done
if [[ -z "$PYLIN" ]]; then
  echo "    Windows Python not found in the prefix - installing Python 3.11 (additive)"
  echo "    into the same prefix. MT5/Wine config is NOT touched."
  PYVER="3.11.9"; PYFILE="python-$PYVER-amd64.exe"
  TMP="$(mktemp -d)"
  wget -q -O "$TMP/$PYFILE" "https://www.python.org/ftp/python/$PYVER/$PYFILE"
  WINEPREFIX="$PREFIX" WINEDEBUG=-all wine "$TMP/$PYFILE" /quiet InstallAllUsers=1 \
    PrependPath=1 Include_test=0 TargetDir='C:\Python311' || true
  rm -rf "$TMP"
  PYLIN="$PREFIX/drive_c/Python311/python.exe"
fi
[[ -x "$PYLIN" ]] || { echo "ERROR: Windows Python still missing at $PYLIN"; exit 1; }
WYPY="$(echo "$PYLIN" | sed "s|^$PREFIX/drive_c|C:|; s|/|\\\\\\\\|g")"
WBRIDGE="Z:$(echo "$BRIDGE_DIR" | sed 's|/|\\\\\\\\|g')\\bridge.py"
echo "    python: $PYLIN"

echo "==> [3/7] Installing bridge dependencies (inside the prefix's Python)"
WINEPREFIX="$PREFIX" WINEDEBUG=-all wine "$WYPY" -m pip install --quiet \
  MetaTrader5 fastapi 'uvicorn[standard]' pydantic requests

echo "==> [4/7] Writing /etc/forexmind-bridge.env (token + paths)"
cat > /etc/forexmind-bridge.env <<EOF
BRIDGE_TOKEN=$TOKEN
BRIDGE_PORT=$PORT
WINEPREFIX=$PREFIX
DISPLAY=$DISPLAY_VAL
MT5_TERMINAL_EXE=$TERMINAL
EOF
chmod 600 /etc/forexmind-bridge.env

echo "==> [5/7] systemd service forexmind-bridge (runs as '$OWNER' - your MT5's user)"
USER_LINES=""
[[ "$OWNER" != "root" ]] && USER_LINES="User=$OWNER"$'\n'"Environment=HOME=$(getent passwd "$OWNER" | cut -d: -f6)"
cat > /etc/systemd/system/forexmind-bridge.service <<EOF
[Unit]
Description=ForexMind MT5 bridge (Wine, attaches to the existing MT5)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/forexmind-bridge.env
WorkingDirectory=$BRIDGE_DIR
$USER_LINES
ExecStart=/usr/bin/wine "$WYPY" "$WBRIDGE"
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/forexmind-watchdog.service <<EOF
[Unit]
Description=ForexMind bridge watchdog (one pass)
After=forexmind-bridge.service

[Service]
Type=oneshot
EnvironmentFile=/etc/forexmind-bridge.env
$USER_LINES
ExecStart=$REPO_DIR/vps/linux_watchdog.sh
EOF

cat > /etc/systemd/system/forexmind-watchdog.timer <<EOF
[Unit]
Description=Run ForexMind watchdog every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
EOF
chmod +x "$REPO_DIR/vps/linux_watchdog.sh"

echo "==> [6/7] Firewall (ufw if active) - NOTE: also open TCP $PORT in your"
echo "    cloud provider's security group, ufw alone is not enough."
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$PORT/tcp" >/dev/null && echo "    ufw: TCP $PORT allowed"
else
  echo "    ufw not active - skipping (cloud firewall still applies)"
fi

systemctl daemon-reload
systemctl enable --now forexmind-bridge.service >/dev/null 2>&1 || true
systemctl enable --now forexmind-watchdog.timer >/dev/null 2>&1 || true

echo "==> [7/7] Health check (your running MT5 should answer)"
sleep 10
if curl -sf -m 8 -H "X-Bridge-Token: $TOKEN" "http://127.0.0.1:$PORT/health"; then
  echo ""
  echo "SUCCESS - bridge is UP and attached to your existing MT5."
else
  echo "    not answering yet (Wine/Python first-run can be slow). Check:"
  echo "      journalctl -u forexmind-bridge -n 50"
  echo "    then re-test:"
  echo "      curl -s -H 'X-Bridge-Token: <token>' http://127.0.0.1:$PORT/health"
fi
echo ""
echo "No secrets to send anywhere - the cloud already has the same token."
