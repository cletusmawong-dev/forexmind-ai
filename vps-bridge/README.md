# ForexMind AI - MT5 bridge (Windows VPS)

> Full production runbook (auto-start, watchdog, reboot contract, security):
> see [`vps/README.md`](../vps/README.md). This file covers the manual quick-start.

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
