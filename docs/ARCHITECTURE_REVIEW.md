# ForexMind AI architecture review

Phase 1 audit, 2026-11-11. This is an internal review of how the system is built. It does not change strategy rules, the visual UI (that is a separate PR), or the decision that `lifecycle_of` defaults to `LIVE`.

Scope read for this review: `backend/app`, `vps-bridge/bridge.py`, `mt5-connector`, `deploy/`, `frontend/src/lib`, Netlify and Render config, and the backend test suite. The suite was executed on Python 3.12 (see [Tests](#tests)).

## How a trade gets from a candle to a ticket

```
live_loop (backend/app/main.py)
  -> SignalEngine.scan          one user, one market, one timeframe
  -> strategies in STRATEGY_CLASSES
  -> signals document + Telegram
  -> execution.mt5.execute_signal
       gates (account flag, mode, kill switch, daily cap, pretrade)
       -> VPS: POST {MT5_BRIDGE_URL}/execute     one URL, one token
       -> or queue exec_commands for the PC connector
  -> vps-bridge/bridge.py
       one MetaTrader5 module, one terminal, one logged-in account
       -> mt5.order_send
```

The PWA never talks to the bridge. It calls `/api/...` on the same origin. Netlify proxies that to the backend. The backend is the only caller of port 8700.

There is already an accounts registry (`backend/app/api/routes_accounts.py`) and an account switcher in the app. Both describe one *selected* account. The order path still sends every owner order to the single bridge URL. `account_id` is stamped on the signal and is not sent in the `/execute` body (`execution/mt5.py`, the `bridge_post("/execute", ...)` call).

## What to leave alone

- `lifecycle_of` in `backend/app/learning/versions.py` returns `LIVE` when a strategy id is missing from `LIFECYCLE`. The gold strategies rely on that: their docs boot `PAUSED` via `START_PAUSED`, and unpausing them is what turns trading on. That default stays.
- `STRATEGY_CLASSES` is the only door to scanning. It is **not empty**. Six classes are registered: MRMS scalper, pullback trend, liquidity sweep, gold NY pullback, gold NY ORB, gold Asia range. Older comments in that file still describe the 2026-10-10 wipe. Do not add or remove strategies as part of the redesign.
- The owner designs and backtests strategies. The research modules under `strategies/research_only/` and `strategies/tpc15/` stay unregistered on purpose.
- `seed_if_empty` must stay uncalled on boot. It writes demo signals when the signals collection is empty. Calling it on the VPS would mix demo history into the live database. Startup already calls `ensure_strategy_docs()` only.

## Findings, highest severity first

### 1. Critical — one terminal for the whole product

The MetaTrader 5 Python package is a process-wide binding to one `terminal64.exe`. `vps-bridge/bridge.py` calls `mt5.initialize()` with no path and never calls `mt5.login()`. The account is whoever is logged in on that terminal. The cloud config is one pair: `MT5_BRIDGE_URL` and `MT5_BRIDGE_TOKEN` (`backend/app/config.py`). `user_mode()` forces every user except `FOREXMIND_OWNER_USER_ID` off the VPS. `bridge_get("/account")`, `bridge_get("/positions")`, and `bridge_get("/quote")` take no login.

`routes_accounts.py` can store several rows (label, broker, server, login, mode, `trading_enabled`). Modes are `signals_only`, `own_pc`, and `vps_bridge`. `vps_bridge` is rejected for anyone except the owner. `execute_signal` loads `active_account()` and then still posts to the global bridge. A registry error is swallowed and the order continues:

```547:548:backend/app/execution/mt5.py
    except Exception:
        pass   # account registry is an isolation ADD-ON; base flow continues
```

Inside the VPS branch, `acct = bridge_get("/account")` replaces that registry row. Lot size and the pretrade balance check then use the terminal, not the row the user selected.

The app switcher (`frontend/src/components/AccountSwitcher.tsx`) posts `/api/accounts/{id}/select` and reloads the page. The UI can show account B while the bridge still trades account A.

### 2. Critical — a safety read that fails allows the trade

Two separate switches exist, and both can fail open:

- User kill switch: `execution_enabled()` in `execution/mt5.py`, stored on `agent_goals`. Default is blocked (`EXECUTION_KILL_SWITCH` defaults to on). This one is in better shape.
- Global kill switch: `backend/app/risk/killswitch.py` levels 0–5. `check()` returns `(True, "")` when the read throws. The comment in that function records the choice. A down database then looks like "level 0, normal".
- The account-disabled gate above is skipped entirely when the accounts read throws.

`propfirm.py` can shrink a lot from per-account drawdown rules, and a failure there is logged while the uncapped lot continues (`execution/mt5.py` around the prop-firm cap). The signal engine also swallows any exception from `execute_signal` (`engine/signal_engine.py`), so a crash after the broker accepted the order can leave the app with no ticket stored.

### 3. Critical — order placement is not idempotent, and the lock is local

`bridge_post` is a single `requests` call. Network errors become `{"error": "bridge unreachable: ..."}`. There is no retry, and there is no second call to ask the terminal "did this comment already fill?" before sending again.

The bridge copies `signal_id[:31]` into the MT5 comment and does not look for an existing position with that comment (`vps-bridge/bridge.py` `/execute`). A timeout where MT5 filled and the HTTP response was lost will be retried by a human or by a future retry loop as a second order.

Dedup on the cloud is `_setup_already_sent`: same market, direction, candle time, and strategy, status in `SUBMITTED` / `FILLED` / `EXECUTING`. It runs under `_exec_lock`, a `threading.Lock()` in the API process. A second Uvicorn worker, a second container, or a restart that loses the in-memory `_CONFIRMED_POSITIONS` set does not share that lock. The Firestore list cache has already caused a double send once; the comment in `db/store.py` records it.

`exec_events` is the closest thing to an order audit log (`execution/ledger.py`, written by `_emit`). `_emit` swallows its own errors. `audit_log` is the admin/RBAC log, not an order log. There is no row that is written *before* `order_send` and then updated with the retcode in the same transaction as the send, because the send happens on another machine.

### 4. High — auth defaults are safe only if every environment variable is set

| Item | Where | What happens if the env var is missing |
| --- | --- | --- |
| JWT secret | `config.py` `jwt_secret` | Tokens are signed with `dev-only-secret-change-me`. Anyone who knows the string can mint a 30-day token (`token_ttl_hours = 24 * 30`). The token is HMAC-SHA256, not a library JWT with `iss`/`aud`. |
| Passwords | `core/security.py` `hash_password` | One SHA-256 of `salt + password`. Fast to brute-force offline. |
| CORS | `main.py` and `vps-bridge/bridge.py` | `allow_origins=["*"]` on the trading API and on the bridge. |
| Telegram webhook | `config.py` default `fxm-hook`; `routes_telegram.py` | A wrong secret returns `{"ok": true}` and drops the update. The path secret is also the only check. |
| Bridge | `bridge.py` binds `0.0.0.0:$PORT` (8700), header `X-Bridge-Token` | Plain HTTP. The token is the only control. `deploy/vps/setup.sh` probes `http://169.58.98.139:8700/health` with `X-Bridge-Token: 20260914`, which is the order magic number, not the bridge secret. |
| Connector | `POST /api/execution/connector/*` | Authenticated by the `X-Pairing` code stored on `agent_goals`, not by the user token. |

`GET /api/agent/activity` lists `agent_activity` globally (limit 400) for any logged-in user. `GET /api/markets` and `GET /api/health` are public, which is acceptable. `GET /api/system/info` is worth re-checking before it is left public; it is unauthenticated today.

No broker password is stored in the repo. Logins on `trading_accounts` are plain strings in the JSON document (the MT5 account number, not the password). The password lives only in the MT5 terminal profile on the VPS. That part is the right shape, and it does not scale to five logins until the terminal layout does.

### 5. High — three different answers for "where is the API"

| File | API target |
| --- | --- |
| `netlify.toml` (repo root) | `http://169.58.98.139:8000` (VPS). `base = "frontend"`. |
| `frontend/public/_redirects` | Same VPS address. |
| `frontend/netlify.toml` | `https://forexmind-ai-api.onrender.com` |
| `render.yaml` | Still defines the Render web service, `REPLAY_ENABLED=1`, auto-deploy. |
| `.github/workflows/keep-alive.yml`, `scripts/keep_alive.py`, `mt5-connector` default `CLOUD_URL` | Render host. |
| `DEPLOY.md` | Render + Netlify. |
| `docs/VPS_MIGRATION.md` | Says the VPS was not purchased yet. `deploy/vps/` and `deploy/auto-deploy.sh` contradict that. |

The frontend client (`frontend/src/lib/api.ts`) uses relative `/api/...` and stores the token in `localStorage` under `fm_token`. Whichever Netlify config the site build actually picks is the backend the phones hit. A site built with `frontend/` as the package root can load `frontend/netlify.toml` and proxy to Render while the root file says Render is retired. That is a production split-brain, not a comment drift.

`deploy/auto-deploy.sh` fetches `origin/main` and runs `git reset --hard` every two minutes (`fxm-deploy.timer`). A merge to `main` is a production deploy of the backend and, when `vps-bridge/` changes, a bridge restart. Work lands on a branch and a pull request. This review does not touch `main`.

### 6. High — the accounts table is not in the schema the owner was given

`db/store.py` lists `trading_accounts` in `COLLECTIONS`. `backend/scripts/supabase_schema.sql` creates one `(id text, data jsonb)` table per collection and never creates `trading_accounts`. The same file is missing later collections (`evidence`, `debates`, `incidents`, `invites`, `community_posts`, and others added after the SQL was written). `SupabaseStore` init failure falls through to `LocalStore` (`data/db.json`) with no failed health check. On the VPS that file is inside the container and is not the broker.

`list_accounts` turns a missing table into `{accounts: [], note: ...}`. `add_account` returns 503. So the multi-account UI can look empty on production even when the code is deployed.

`seed_if_empty` is imported in `main.py` and has no caller. Boot uses `ensure_strategy_docs()` only. Leave that split. Do not "fix" it by calling the seeder.

### 7. High — the six-hour learning pass calls a function that does not exist

`live_loop` in `main.py` does `for _uid in (await _user_ids())`. There is no `_user_ids` in the module. The `except` logs nothing. The discover pass never runs. The rest of that tick uses `State.store.list("users")`, which does exist. The loop itself is one asyncio task with no single-flight lock across processes. `State` (`backend/app/state.py`) holds one store, one provider, and one `SignalEngine` for every user.

### 8. Medium — risk, P&L, and positions are per user

- Dollar risk is on the user `settings` document (`kind: risk`, `risk_per_trade_usd`), with a fallback to `agent_goals` and a default of $50 (`execution/mt5.py`). Some strategies override that with their own `risk_usd` before sizing.
- Daily trade cap is `EXECUTION_MAX_TRADES_PER_DAY` (default 6) counted on the user's signals, not on an account.
- `GET /api/positions/live` (`routes_journal.py`) reads one bridge and keeps positions whose magic equals the global `MAGIC` (20260914 in the bridge).
- Home balance uses that same bridge equity when mode is `vps` and the probe says online.
- `propfirm.py` is written per `trading_accounts` document, and the execution path does not pass that document's balance. It passes the terminal snapshot, after the variable shadowing in finding 1.
- The client lot card (`frontend/src/lib/position.ts`) uses a static pip table. It is a hint. Sizing that goes to the broker is server-side.

A serious book needs, per login: max loss per day, max open risk, max positions, max lot, which strategies may trade, and a kill switch that stops that login only. The global kill switch can stay as the "stop everything" control.

### 9. Medium — the execution module is doing too many jobs, and it imports the HTTP layer

`backend/app/execution/mt5.py` is about 1,700 lines: HTTP to the bridge, lot math, prop-firm cap, pretrade, notifications, deal sync, and the connector queue. `aimanager/engine.py` is about 1,100 lines and keeps position-review state in memory. `execution/mt5.py` imports `api.routes_accounts.active_account`. `risk/killswitch.py` imports the admin emergency-stop helper. Those edges make a second account a cross-cutting edit instead of a new type.

Process-global mutable state that will mis-attribute data once two terminals exist:

| State | File | Why it matters |
| --- | --- | --- |
| `_exec_lock` | `execution/mt5.py` | One lock for every order. Account B waits on account A's broker round-trip. Not shared across workers. |
| `_CONFIRMED_POSITIONS` | `execution/mt5.py` | In-memory set. Restart replays deal sync. |
| `_symbol_info_cache` | `execution/mt5.py` | Pip value from whichever terminal answered last. Brokers differ. |
| `TradeManager._state` | `aimanager/engine.py` | One manager, keyed by ticket. Tickets are unique per terminal, not across terminals. |
| `FirestoreStore._cache` | `db/store.py` | Stale counts have double-fired signals. |
| `get_store()` singleton | `db/store.py` | `LocalStore` is one JSON file. Two workers corrupt it. |
| Bridge `_mt5_lock` | `vps-bridge/bridge.py` | Correct for one terminal. `GET /symbol_info` still calls `symbol_select` / `symbol_info` outside the lock. |

`main.py` registers `routes_evidence` and `routes_research` twice in the import list. Harmless, and a sign the app module is accreting. The FastAPI description still says "signals only, never execution."

### 10. Medium — tests were green except three, and CI never ran them

Measured on this branch's base (`main` at the audit), Python 3.12, dependencies from `backend/requirements.txt` plus httpx 0.28.1 installed by hand:

`3 failed, 968 passed` in about 109 seconds. After the fixes on this branch the same command reports `971 passed` in about 116 seconds.

The failures were not a block of 20. The three were:

1. `test_strategy_behaviour_unchanged_since_baseline` — the fixture hashed `ImportError: cannot import name 'atr' from 'app.strategies.base' (/home/user/forexmind-ai/...)`. On this machine the path is `/workspace/...`. The rule output of every strategy matched. The dormant supply-and-demand module still raises that import inside `compute()` (`from ..base import atr`). It is unregistered. The harness now strips absolute paths, and the fixture records the path-free error. Behaviour hashes of the other eight strategies are unchanged.
2. `test_owner_three_are_the_only_registered_strategies` and `test_owner_wipe_boot_purges_unregistered_docs` — these pass in isolation. In the full run, `test_master_upgrade.py` deletes `strategy_2_supply_demand_fvg` with `monkeypatch.delitem` while `with_strategies` has added it. The fixture restored the dict, then monkeypatch's undo inserted the key again. Later tests saw a strategy that production does not register. `with_strategies` now adds keys through the same monkeypatch stack, so the undo removes them.

`httpx` was absent from `backend/requirements.txt`. Nothing in `backend/app` imports it. `fastapi.testclient.TestClient` does, via Starlette. A clean `pip install -r requirements.txt` cannot collect the API tests. This branch pins `httpx==0.28.1`. Starlette warns that `httpx2` will replace it later; the suite passes on 0.28.1 today.

GitHub Actions ran an Android build and a Render keep-alive ping. It did not run pytest. `.github/workflows/backend-tests.yml` runs `python -m pytest tests -q` on pull requests.

## Smaller notes

- Reconnect for the VPS path is "skip the signal if `/account` fails". `expire_stale_commands` covers the PC connector queue, not a bridge order that timed out.
- The watchdog (`vps/watchdog.py`) restarts one terminal and one bridge. A dead terminal is a full trading outage.
- `on_event("startup")` is the deprecated FastAPI hook. There is no shutdown path that stops `live_loop` cleanly.
- `keep_warm.py` and the provider throttle use `time.sleep` off the request path. Acceptable. They will surprise anyone who moves that code onto a request.
- Community and accounts both treat a missing Supabase table as an empty successful read. Easy to mistake for "the user has no data".

## Suggested shape after the redesign

Keep one FastAPI process as the brain: signals, risk policy, audit. Teach it a `BrokerSession` per account (base URL, token, magic, risk, strategy allow-list) instead of reading `settings.bridge_url` inline.

Move `active_account` out of `api/routes_accounts.py` into a small accounts service the execution code can import without depending on the router.

Split `execution/mt5.py` into transport, sizing, and the order state machine when the fan-out work starts. Doing that split in the same pull request as the first live second-account order is how a revert gets expensive. The phased plan in `docs/MULTI_ACCOUNT_DESIGN.md` separates them.

Security hardening (JWT, password hash, CORS, webhook status, bridge transport) is its own pull request. It can lock the owner out if it ships in the same change as a bridge restart, and `auto-deploy.sh` restarts the bridge whenever `vps-bridge/` changes on `main`.
