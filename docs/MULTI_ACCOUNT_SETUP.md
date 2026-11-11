# Setting up MT5 terminals 2 to 5

Slot 1 is the bridge already running on port 8700. Do not restart it while you follow this page. The steps below add four more terminals and four more bridge processes. They do not place trades until you turn `trading_enabled` on for that account in the app.

## What you are installing

Each extra account is one portable MetaTrader 5 terminal and one Python process that talks only to that terminal. The process listens on `127.0.0.1` and requires the same `X-Bridge-Token` header the current bridge uses. It reads `MT5_LOGIN`, `MT5_PASSWORD`, and `MT5_SERVER` from a file on the VPS and calls `mt5.login` when it starts.

| Slot | Port | Env file | Terminal folder |
| --- | --- | --- | --- |
| 1 (already running) | 8700 | the existing bridge env | the existing terminal |
| 2 | 8701 | `C:\ForexMind\secrets\accounts\2.env` | `C:\ForexMind\terminals\slot2` |
| 3 | 8702 | `...\accounts\3.env` | `...\slot3` |
| 4 | 8703 | `...\accounts\4.env` | `...\slot4` |
| 5 | 8704 | `...\accounts\5.env` | `...\slot5` |

## Resources

These are planning numbers from typical MT5 desktop use, not a measurement of this VPS. Check Task Manager before you start all four.

| Piece | RAM | CPU while the market is open |
| --- | --- | --- |
| One MT5 terminal | about 400–800 MB | about 5–15% of one core; gold during news is higher |
| One bridge process | about 80–150 MB | negligible |
| Slots 2–5 together | plan for 3–5 GB free RAM | plan for 1–2 cores free on top of slot 1 |

If the box is already near its memory limit with slot 1, add one terminal, watch it for a session, then add the next. Do not start all four on a machine that has less than about 4 GB free.

## Steps

1. On the VPS, open PowerShell in the repo and run:

   ```
   powershell -ExecutionPolicy Bypass -File vps\install-extra-slots.ps1
   ```

   The script writes the env files and scheduled tasks. It does not stop the service on port 8700.

2. Copy the MT5 terminal you already use into each slot folder as a portable copy. In the existing terminal: File → Open Data Folder, then run the terminal once from the new folder with the `/portable` switch so it keeps its own data directory:

   ```
   C:\ForexMind\terminals\slot2\terminal64.exe /portable
   ```

   Log into that slot's account once in the GUI so you know the server name, then close that window. The bridge will log in itself after this.

3. Edit `C:\ForexMind\secrets\accounts\2.env`. Set `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`. Leave `BRIDGE_HOST=127.0.0.1`. Use the same `BRIDGE_TOKEN` as slot 1. Repeat for 3, 4, and 5 when you are ready. An empty login means that slot stays stopped. The watchdog will not start it.

4. The API runs in Docker and cannot see `127.0.0.1` on the Windows host. In `deploy/vps/docker-compose.yml` the service has `extra_hosts: host.docker.internal:host-gateway`. After you deploy that compose change, restart only the API container. Do not restart the bridge on 8700 as part of adding a terminal. From inside the API container, slot 2 is `http://host.docker.internal:8701`.

5. In the app, add the account (or edit the row) and set:

   - `bridge_slot`: 2
   - `bridge_url`: `http://host.docker.internal:8701`
   - `symbol_suffix`: empty, unless this login's symbol names differ
   - `strategy_ids`: the strategies this login should take. Empty means it takes nothing
   - `risk_per_trade_usd`: this login's dollar risk. Leave empty to keep using the user-level figure
   - `trading_enabled`: leave off until health is green

6. Start only that slot:

   ```
   schtasks /Run /TN ForexMindBridgeSlot2
   ```

   Then, on the VPS itself:

   ```
   curl -s -H "X-Bridge-Token: YOUR_TOKEN" http://127.0.0.1:8701/health
   ```

   The login in the JSON must be the login in the env file. If it is not, the cloud will not send that account an order.

7. Schedule `vps\watchdog_slots.py` once a minute. It checks ports 8701–8704 only. The existing watchdog keeps port 8700.

8. When health is green, turn trading on for that account in the app. Until then the slot is health-only: the poller records equity and connected, and no order is sent.

## What a merge does to slot 1

`deploy/auto-deploy.sh` restarts the bridge service when files under `vps-bridge/` change on `main`. The new bridge code leaves slot 1's behavior the same when `MT5_LOGIN` is unset and `BRIDGE_SLOT` is unset: it still calls `mt5.initialize()` with no path, it does not call `mt5.login`, and it still listens on `0.0.0.0:8700` with the token header. A restart from auto-deploy brings slot 1 back in that mode. It does not start slots 2–5. Those start only from the scheduled tasks above.

Merge when you can tolerate that one restart of port 8700. Adding terminals 2–5 does not require you to restart port 8700 yourself.
