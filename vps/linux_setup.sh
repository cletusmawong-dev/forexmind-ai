#!/usr/bin/env bash
# =============================================================================
# ForexMind bridge setup for an UBUNTU VPS where MT5 ALREADY RUNS UNDER WINE.
#
# WHAT THIS SCRIPT DOES (and does NOT do):
#   DOES:  detect your EXISTING Wine prefix + MT5 terminal and attach to them;
#          add Windows Python INSIDE the same Wine prefix if missing (required:
#          the MetaTrader5 pip package is Windows-only); install the bridge as
#          a systemd service (auto-start after reboot) + a 1-minute watchdog
#          timer; open the bridge port in ufw if ufw is active.
#   NEVER: installs or reinstalls MetaTrader 5, changes Wine configuration,
#          touches your MT5 account/login, or edits anything inside the MT5
#          installation directory.
#
# Usage (from the repo root on the VPS):
#   sudo bash vps/linux_setup.sh --token 'your-long-secret'
#   sudo bash vps/linux_setup.sh --token '...' --port 8700 --prefix /home/you/.wine
# =============================================================================
set -euo pipefail

TOKEN="" ; PORT="8700" ; PREFIX_ARG="" ; DISPLAY_VAR=":0"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token)  TOKEN="$2"; shift 2 ;;
    --port)   PORT="$2"; shift 2 ;;
    --prefix) PREFIX_ARG="$2"; shift 2 ;;
    --display) DISPLAY_VAR="$2"; shift 2 ;;
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
PREFIXES=()
[[ -n "$PREFIX_ARG" ]] && PREFIXES+=("$PREFIX_ARG")
[[ -n "${WINEPREFIX:-}" ]] && PREFIXES+=("$WINEPREFIX")
for d in "$HOME"/.wine "$HOME"/.wine-* /root/.wine /home/*/.wine /home/*/.wine-*; do
  [[ -d "$d" ]] && PREFIXES+=("$d")
done
# unique, existing prefixes that actually contain drive_c
U=(); for p in "${PREFIXES[@]:-}"; do
  [[ -n "$p" && -d "$p/drive_c" ]] && [[ ! " ${U[*]} " == *" $p "* ]] && U+=("$p")
done
((${#U[@]})) || { echo "ERROR: no Wine prefix found (looked in /home/*/.wine, /root/.wine)."; exit 1; }

TERMINAL="" ; T_PREFIX=""
for p in "${U[@]}"; do
  hit="$(find "$p/drive_c" -maxdepth 5 -iname 'terminal64.exe' 2>/dev/null | head -1 || true)"
  if [[ -n "$hit" ]]; then TERMINAL="$hit"; T_PREFIX="$p"; break; fi
done
[[ -n "$TERMINAL" ]] || { echo "ERROR: terminal64.exe not found in any prefix (${U[*]}). MT5 must already be installed."; exit 1; }
PREFIX="$T_PREFIX"
WTERMINAL="$(echo "$TERMINAL" | sed "s|^$PREFIX/drive_c|C:|; s|/|\\\\\\\\|g")"
echo "    Wine prefix : $PREFIX"
echo "    MT5 terminal: $TERMINAL"
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
  WINEPREFIX="$PREFIX" wine "$TMP/$PYFILE" /quiet InstallAllUsers=1 PrependPath=1 \
    Include_test=0 TargetDir='C:\Python311' || true
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
DISPLAY=$DISPLAY_VAR
MT5_TERMINAL_EXE=$WTERMINAL
EOF
chmod 600 /etc/forexmind-bridge.env

echo "==> [5/7] systemd service forexmind-bridge (auto-start on boot)"
cat > /etc/systemd/system/forexmind-bridge.service <<EOF
[Unit]
Description=ForexMind MT5 bridge (Wine)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/forexmind-bridge.env
WorkingDirectory=$BRIDGE_DIR
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
chmod +x "$REPO_DIR/vps/linux_watchdog.sh" 2>/dev/null || true

echo "==> [6/7] Firewall (ufw if active) - NOTE: also open TCP $PORT in your"
echo "    cloud provider's security group (IBM Cloud etc.), ufw alone is not enough."
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$PORT/tcp" >/dev/null && echo "    ufw: TCP $PORT allowed"
else
  echo "    ufw not active - skipping (cloud firewall still applies)"
fi

systemctl daemon-reload
systemctl enable --now forexmind-bridge.service >/dev/null 2>&1 || true
systemctl enable --now forexmind-watchdog.timer >/dev/null 2>&1 || true

echo "==> [7/7] Health check (MT5 must be running & logged in - it already is)"
sleep 8
if curl -sf -m 8 -H "X-Bridge-Token: $TOKEN" "http://127.0.0.1:$PORT/health"; then
  echo ""
  echo "SUCCESS - bridge is UP and attached to your existing MT5."
else
  echo "    bridge not answering yet (Wine/Python first-run can be slow) -"
  echo "    check: journalctl -u forexmind-bridge -n 50 ; then retry manually:"
  echo "    curl -s -H 'X-Bridge-Token: ***' http://127.0.0.1:$PORT/health"
fi
echo ""
echo "Give the assistant ONLY this confirmation - the token stays on the VPS/Render."
