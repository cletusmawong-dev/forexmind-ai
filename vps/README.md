# VPS RUNBOOK — Windows VPS is the permanent runtime (Phase 2)

The VPS is PURCHASED and is now part of the production architecture.
The user's personal computer is NOT part of the operating design.

## Topology (hybrid - zero migration)

```
  Phone PWA (Netlify)  ->  Render FastAPI cloud brain (24/7, always on)
                                   |
                                   | HTTPS, token (X-Bridge-Token)
                                   v
  WINDOWS VPS  =  MetaTrader 5 (Exness)  +  bridge.py (:8700)  +  watchdog
```

- The cloud brain stays on Render (already deployed, live data, your app).
- The VPS runs ONLY what must sit next to MT5: the bridge service and the
  watchdog. It does NOT need the backend, database or frontend.
- Everything auto-starts after reboot. Nobody logs in with RDP to "start it".

---

## 1. One-time setup (about 20 minutes)

1. RDP into the VPS. Run Windows Update once; reboot if it asks.
2. Install MetaTrader 5 from Exness. Log in to the **DEMO** account.
   In MT5: Tools -> Options -> Expert Advisors -> tick
   "Allow algorithmic trading". Leave the terminal OPEN.
   Note the terminal path, e.g. `C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe`.
3. Install Python 3.11+ from python.org - tick "Add python.exe to PATH".
4. Copy the repo folder to the VPS, e.g. `C:\forexmind-ai`
   (smallest path: download the GitHub ZIP of the latest commit and extract).
5. Open PowerShell **as Administrator** in `C:\forexmind-ai\vps` and run:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass -Force
   .\install.ps1 -BridgeToken "paste-a-long-random-secret-here"
   ```

   `install.ps1` does all of this for you:
   - `pip install -r vps-bridge\requirements.txt`
   - firewall rule: inbound TCP 8700 named "ForexMind Bridge"
   - machine env vars: `BRIDGE_TOKEN` (+ optional `MT5_TERMINAL_EXE`)
   - scheduled task **ForexMindBridge**  - starts the bridge at every boot (SYSTEM)
   - scheduled task **ForexMindWatchdog** - every minute, heals what died

6. Reboot the VPS once and verify (no login needed after reboot):
   ```powershell
   curl.exe -s -H "X-Bridge-Token: %BRIDGE_TOKEN%" http://127.0.0.1:8700/health
   ```
   Expect `"ok": true` and your demo account `login`/`balance`.

---

## 2. What the watchdog heals (every minute, one idempotent pass)

| Condition detected                          | Automatic action                          |
|---------------------------------------------|-------------------------------------------|
| bridge not answering on 127.0.0.1:<port>    | start `pythonw vps-bridge\bridge.py`      |
| bridge up but MT5 terminal not connected    | start `terminal64.exe` if not running     |
| bridge up, terminal up, `trade_allowed=false`| log only (you must enable Algo Trading)  |
| anything else                               | append to `vps\watchdog.log`              |

The watchdog is stdlib-only (no extra packages), logs every pass with a
timestamp, and is safe to run every minute (one short pass, then exits).
See `vps\watchdog.log` first when diagnosing; keep last ~500 lines
(it self-trims).

---

## 3. Reboot contract (what happens after a VPS restart)

Automatic, in order:
1. Windows starts -> scheduled task `ForexMindBridge` launches the bridge.
2. The bridge initializes MT5 (`mt5.initialize()` inside `/health`/`/account`)
   and reports terminal + account state.
3. The watchdog pass (within a minute) starts anything that failed.
4. The cloud backend calls the bridge lazily on next use; an unreachable
   bridge NEVER sends orders - entries degrade to advisory with honest
   `SKIPPED_BRIDGE_OFFLINE` logs.

**No duplicate trades after restart (guarantee):**
- The entry path is unchanged: a signal executes at most once (signals are
  marked with execution status; queued manual commands expire after 15 min).
- Position/deal state is always re-read FROM THE BROKER (`/positions`,
  `/deals`) - the cloud never assumes what the VPS "must have" open.
- Full AI-manager state rebuild + reconciliation runs in Phase 7/8 and is
  exercised by the Phase 12 restart tests before anything goes live.

---

## 4. Wire the cloud to this VPS (env-only, done by the assistant)

Provide the VPS IP + the BRIDGE_TOKEN you chose, then these are set in the
Render dashboard (never in code, never in the frontend):

```
EXECUTION_MODE=mt5_bridge
MT5_BRIDGE_URL=http://<VPS-IP>:8700
MT5_BRIDGE_TOKEN=<your long secret>
```

The kill switch stays ON (no auto-trading) until Phase 12 demo testing
passes and YOU flip it in Settings.

---

## 5. Security baseline

- RDP: strong password; restrict source IP in the provider firewall if you
  can; keep Windows Update on.
- The bridge accepts only requests carrying `X-Bridge-Token`.
- Port 8700 is open only for the bridge; the token is the real gate.
- Secrets live in machine env vars / Render env - never in the repo, never
  in the phone frontend.
- Demo account first. Real money is a separate, explicit decision.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `/health` 503 "terminal not reachable" | open MT5 and log in; keep it running |
| `trade_allowed: false` | enable Algo Trading button in MT5 toolbar |
| bridge answers locally, not from cloud | firewall rule (run install.ps1 as Admin) + correct IP in Render env |
| orders fail with symbol errors | broker suffix (XAUUSDm etc.) - the bridge resolves aliases; add yours to ALIASES in bridge.py |
| watchdog log spams "started bridge" | check `pythonw` path + BRIDGE_TOKEN machine env |

## 7. Uninstall / disable auto-start

```powershell
schtasks /Delete /TN ForexMindBridge /F
schtasks /Delete /TN ForexMindWatchdog /F
netsh advfirewall firewall delete rule name="ForexMind Bridge"
```
