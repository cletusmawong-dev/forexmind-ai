# ForexMind AI - MT5 bridge (Windows VPS)

> Full production runbook (auto-start, watchdog, reboot contract, security):
> see [`vps/README.md`](../vps/README.md). This file covers the manual quick-start.

## Bridge contract (v2)

All endpoints require the `X-Bridge-Token` header. Symbol names are the app's
market names (EURUSD, XAUUSD, NAS100) - resolved to broker symbols here.

- `GET  /health`  -> terminal + account snapshot
- `GET  /account` -> login/balance/equity (used for risk-based lot sizing)
- `POST /execute` -> open a position `{signal_id, symbol, direction, lots, sl, tp, magic}`
- `POST /modify_sl` (v2) -> move the stop `{ticket, sl}`; the existing TP is
  passed through untouched; broker stops-level distance is validated here and
  an illegal SL is rejected with HTTP 400 (nothing reaches MT5)
- `POST /partial_close` (v2) -> close part of a position `{ticket, volume|fraction}`;
  volumes floor to the lot step (never rounded UP), a below-min result is
  rejected, a position smaller than 2 x min-lot cannot be split
- `GET  /positions` -> open positions (the cloud reconciles from this after restarts)
- `POST /close`     -> close a full position `{ticket}`
- `GET  /deals?since=` -> deal history (confirms real W/L back onto signals)

One-time setup (10-15 min):

1. RDP into your Windows VPS.
2. Install MT5 from Exness, log in to your DEMO account.
   In MT5: Tools -> Options -> Expert Advisors -> tick "Allow algorithmic trading".
3. Install Python 3.11+ (python.org, tick "Add to PATH").
4. Copy this folder to the VPS (e.g. C:\forexmind-bridge).
5. Open Command Prompt in that folder:
       pip install -r requirements.txt
       set BRIDGE_TOKEN=make-up-a-long-random-secret
       python bridge.py
   (or edit START.bat with your token and double-click it)
6. Windows Firewall: allow inbound TCP port 8700.
7. Send the assistant:
   - VPS IP address
   - the BRIDGE_TOKEN you chose
   and it will wire the backend to the bridge (EXECUTION_MODE=mt5_bridge).

Notes
- MT5 must stay running and logged in. The bridge talks to that terminal.
- Keep the demo account until you trust it end-to-end. Real money is a
  separate, explicit decision with smaller risk caps.
- HTTP + token is fine for a demo bridge. Nothing here touches real funds.
