# ForexMind AI - Manual MT5 connection (your own PC)

Connects the app to the MT5 terminal running on YOUR Windows PC.
Outbound-only: no router ports, no static IP, nothing to expose.

One-time setup (5-10 min):

1. On the PC: install MT5 (Exness), log in to your DEMO account.
   MT5 -> Tools -> Options -> Expert Advisors -> tick "Allow algorithmic trading".
2. Install Python 3.11+ (python.org, tick "Add to PATH").
3. Copy this folder somewhere (e.g. C:\forexmind-connector).
4. In the app: Settings -> Order execution - MT5 -> choose "Manual (this PC)"
   and copy the pairing code (looks like FXM-A1B2-C3D4).
5. Command Prompt in the connector folder:
       pip install -r requirements.txt
       set PAIRING_CODE=FXM-....-....
       set CLOUD_URL=https://forexmind-ai-api.onrender.com
       python connector.py
   (or edit START.bat with your code and double-click it)
6. The app's Settings card turns green: "PC connected".

What it does
- Executes queued signals in your MT5 (SL/TP attached, magic-tagged).
- Pushes real fills + deal history so W/L comes from the broker, not estimates.

Keep it running while you want auto-execution. Close the laptop lid / shut
down and queued orders simply expire safely after 15 minutes.
