# Multi-account trading design

Up to five MT5 accounts, trading at the same time. Owner decisions from 2026-11-11 are in [Decisions](#decisions-2026-11-11). Phases 2 and 3 are separate pull requests. This file does not change order routing by itself.

Read with `docs/ARCHITECTURE_REVIEW.md`. The current system already stores account rows and shows a switcher. It executes on one terminal. This document is the path from that row to five live logins.

## Recommendation

Run **one MetaTrader 5 terminal per account, and one bridge process per terminal**, up to five, on the same Windows VPS. A small supervisor keeps them up. The cloud backend keeps a directory of those bridges and fans a signal out to the accounts that are allowed to take it. Each account is sized, gated, and recorded on its own. One account's broker error does not cancel the others.

The account that trades today stays on port 8700 until the owner cuts the second terminal in. Nothing in this design requires a strategy change, a change to `lifecycle_of`, or a new entry in `STRATEGY_CLASSES`.

### Why this option

The official `MetaTrader5` Python module is one terminal per process. `mt5.initialize(path=...)` attaches to a specific `terminal64.exe`. `mt5.login()` switches that terminal's session. `account_info()`, `positions_get()`, and `order_send()` always talk to the attached session. There is no account argument. A lock around those calls, which `bridge.py` already has, protects threads inside one process. It does not create a second login.

| Option | Verdict |
| --- | --- |
| One process, call `mt5.login()` before each order | Reject. Login is slow, it drops the other session, and a quote read for account 2 can land on account 1 if a second thread races. Positions and history get mixed. A disconnect logs everyone out. |
| One terminal, five logins stored and switched | Same rejection. MT5 client terminals are single-session. |
| MT5 Manager API | Reject for this product. That API is a broker-side tool. Retail logins do not get it. |
| Five terminals, five Python processes | **Use this.** A hung terminal, a retcode 10016, or a reconnect stays inside one process. The other four keep trading. This matches the way the package is built. |
| Five cloud connectors on five PCs (`mt5-connector`) | Keep as an extra mode for a login that is not on the VPS. The owner's "trade five at once" case is the VPS, where the machine is always on. The connector remains the path for `own_pc`. |

Five portable copies of the terminal (MT5's `/portable` install) are the usual way to pin each data directory to one login. Each copy gets its own bridge:

| Slot | Terminal | Bridge listen | Cloud env |
| --- | --- | --- | --- |
| 1 (current) | Existing install, current login | `127.0.0.1:8700` until cutover, then stay or move | Today's `MT5_BRIDGE_URL` |
| 2–5 | Portable copies | `127.0.0.1:8701` … `8704` | Rows in `trading_accounts`, not five new global env vars |

The bridge process does not choose among logins. It serves the terminal it was started against. `initialize(path=<that terminal64.exe>)` makes the binding explicit so a second copy on the machine cannot steal the session.

The cloud side learns the map from the account row: `bridge_url` + `bridge_token_ref`. Tokens stay in the VPS env / a mode-600 file. The API stores a reference (`slot-2`), not the token, once slot routing exists. Until that PR, slot 1 keeps using `MT5_BRIDGE_TOKEN` from the environment so today's deploy does not change.

### What the bridge process does on start

1. `mt5.initialize(path=MT5_TERMINAL_PATH)`.
2. If `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` are set in that process's env, `mt5.login(...)` once. If they are unset, behave as today: use the session already logged in. That keeps the current terminal working with no password in the environment.
3. `GET /health` returns login, server, trade_allowed, and balance. The supervisor and the cloud both use this to see that the process is still the login it claims to be. A health payload whose login does not match the account row is a hard stop for that account's orders.
4. Reconnect: if `terminal_info()` is empty, `initialize` again, then `login` again when credentials are configured. Failure of this loop marks the account `connected: false` and skips its orders. It does not restart the other four.

`vps/watchdog.py` becomes five health checks. Restart terminal N and bridge N. Leave the others running.

## Data model

Stay on the existing document store: one Supabase table per collection, `id text` + `data jsonb`. `trading_accounts` is already a collection name in `db/store.py` and is missing from `scripts/supabase_schema.sql`. The first schema PR adds that table (and the other collections added after the SQL file was last updated) for the owner to run in the Supabase SQL editor. The app does not auto-migrate production.

Extend the account document. Existing fields stay (`label`, `broker`, `server`, `login`, `mode`, `is_active`, `is_default`, `trading_enabled`, `connected`, `currency`, `userId`).

New fields, all optional so old rows still load:

| Field | Purpose |
| --- | --- |
| `bridge_url` | Base URL of this login's bridge. Empty means "use the legacy `MT5_BRIDGE_URL`" for the single current account only. |
| `bridge_slot` | `1`–`5`. Supervisor and logs use it. |
| `credential_ref` | Name of the VPS secret (`accounts/2.env`). Never a password. |
| `magic` | Integer, unique per account. Default for slot 1 remains `20260914` so open tickets still match. |
| `risk_per_trade_usd` | This login's dollar risk. Empty means the user-level figure still applies, so the live account's lot size does not change until the owner sets a number. A strategy `risk_usd` still overrides this inside the account. |
| `risk_pct_cap` | Hard ceiling. The user-level `EXECUTION_RISK_PCT_CAP` still applies if this is empty. |
| `max_daily_loss_usd` | Realized + floating today. Breach stops new entries on this account. |
| `max_trades_per_day` | Replaces the single global cap for this login once set. |
| `max_positions` | Open engine positions on this login. |
| `max_lot` | Broker min/max still wins; this is ours. |
| `strategy_ids` | Pick-list of registry ids this login may trade. Empty means none selected (a new account takes nothing). The slot-1 backfill copies the strategies registered today so the live login does not go silent when fan-out starts enforcing the list. |
| `kill_switch` | `false` stops new entries on this account. Independent of the global levels in `risk/killswitch.py`. |
| `prop_rules` | Already read by `propfirm.py`. Keep it. Feed it this account's balance, not the other terminal's. |
| `symbol_suffix` | Optional suffix appended to the app symbol (`m`, `.m`). Default empty. All five logins are at the same broker today, so this stays blank unless a symbol name ever differs. |

Cap: `POST /api/accounts` rejects the sixth row for that user with 409. Five is the product limit in this design.

`is_active` today means "the one account the app is looking at" and "the one account that may trade". Split those. `is_active` stays the **view** (one row, what the dashboard shows). `trading_enabled` is the **execution** flag, and more than one row may have it true. The switcher stops calling `location.reload()` as a stand-in for a context switch once the API filters by `account_id`. That UI wiring is the read-model PR, coordinated with the visual redesign so the two PRs do not edit the same components blindly.

Signals stay one research document per setup (market, strategy, candle, direction). Execution becomes a child row, one per account that was attempted:

Collection `account_orders` (new), or a nested list on the signal if a new collection is too heavy for the first PR. A collection is cleaner for queries ("all orders for account 2 today").

| Field | Purpose |
| --- | --- |
| `signal_id`, `signal_doc_id`, `account_id`, `userId` | Join keys. |
| `idempotency_key` | `"{signal_id}:{account_id}"`. Unique. The send is refused when a row in `REQUESTED` / `SENT` / `FILLED` already exists. |
| `status` | `SKIPPED` / `REQUESTED` / `SENT` / `FILLED` / `REJECTED` / `FAILED` / `UNKNOWN`. |
| `lots`, `sl`, `tp`, `magic`, `request` | What we intended. |
| `ticket`, `price`, `retcode`, `error` | What the bridge returned. |
| `createdAt`, `updatedAt` | Audit. |

`UNKNOWN` is the timeout case: the HTTP call did not return, so we do not know if `order_send` ran. The reconciler asks that account's bridge for an open position or a deal whose comment starts with this signal id before anyone sends again. `UNKNOWN` blocks a retry until reconcile finishes.

`exec_events` keeps the timeline (requested, sent, filled, failed) and gains `account_id` on each row. `audit_log` stays the admin log. The order audit is `account_orders` + `exec_events`, and `_emit` must not swallow a failure of the `REQUESTED` write: if we cannot record the attempt, we do not send.

## Credentials for five logins

MT5 passwords do not go in Supabase, Firestore, `LocalStore`, the frontend, or Telegram.

Each bridge process reads its own env file on the VPS, mode 600, outside the git checkout (`/opt/forexmind/secrets/account-<slot>.env`):

```
MT5_TERMINAL_PATH=C:\MT5\account2\terminal64.exe
MT5_LOGIN=12345678
MT5_PASSWORD=...
MT5_SERVER=Broker-Server
BRIDGE_TOKEN=...
BRIDGE_PORT=8701
```

The API stores `login`, `server`, `broker`, `bridge_url`, and `credential_ref`. The owner creates the file over RDP or the existing SSH path. The app's "add account" form collects label, broker, server, and login. It does not collect the password. A short setup note tells the owner to drop the env file and start the slot. `connected` flips true when `/health` reports that login.

Rotating a password is a file edit plus a bridge restart for that slot. The other slots stay up.

Passwords live in that VPS env file. Each bridge process calls `mt5.login` once at start. The cloud never sees the password. Rotating one slot restarts only that process. The current login on port 8700 is not restarted to add the file for a later slot.

## Fan-out

`SignalEngine.scan` keeps producing one signal. It does not learn about accounts. After the signal document is stored, a dispatcher (new function next to `execute_signal`, called from the same place) does this:

1. Load the user's `trading_accounts` (hard cap 5). A failed read of a provisioned registry does not send. A missing table (SQL not applied yet) keeps today's single-bridge path so the live login is not stopped early.
2. Keep rows with `trading_enabled`, `kill_switch` not set, `mode` in `vps_bridge` or `own_pc`, and this strategy id present in `strategy_ids`. An empty pick-list takes nothing.
3. Apply the global kill switch. On a read error, skip every account (fail closed).
4. For each remaining account, build an intent. Sizing uses **that** bridge's `/account` equity and **that** account's risk fields. Symbol rules use **that** bridge's `/symbol_info`. A strategy-level `risk_usd` still overrides the account default, per the gold-pair guide, and the override is applied per account (profit-protect uses that account's equity).
5. Run the intents concurrently, one worker per account, pool size 5. Each worker has its own try/except. The result is written to that `account_orders` row. Workers do not share `_exec_lock`. The lock becomes per `account_id`.
6. The signal's `execution_status` becomes a summary: `FILLED` if at least one account filled, `PARTIAL` if some filled and some failed, `FAILED` if all failed, `SKIPPED` if none were eligible. The per-account truth is the child rows. The UI must not show a single green tick when only one of three accounts filled.

`own_pc` accounts stay on the connector queue, with the command tagged `account_id`. The pairing code becomes per account, not per user, when that account's mode is `own_pc`. That can wait until a non-VPS account is actually in the five.

### Isolation rules

- No shared mutable broker cache. Symbol info and pip value are keyed by `account_id`.
- Magic is per account. Position sync filters `magic` and `account_id`. Ticket `12345` on two brokers is two positions.
- AI trade manager state is keyed by `(account_id, ticket)`.
- A pretrade reject (stops too tight, daily loss hit, strategy not allowed) records `REJECTED` for that account and the other workers continue.
- A bridge 503 or connection error records `FAILED` or `UNKNOWN` for that account and the other workers continue.
- Telegram: one message per signal. Each account's result is a line in that message.

### Idempotency, retry, reconnect

Before `order_send`, the worker inserts `account_orders` in `REQUESTED` with the idempotency key. A unique conflict means this intent already exists; the worker returns.

The bridge `/execute` body gains `idempotency_key` and `magic`. The bridge refuses the send when an open position or a deal from the last 48 hours has the same magic and a comment equal to the signal id prefix. That is the cross-check for a cloud crash between send and response. MT5 comments are 31 characters. Signal ids must stay within that, or the bridge stores the full key in a side file. Today's code already truncates to 31. The follow-up PR should check the real id length before relying on the comment.

Retries: at most 2, only for connection errors and HTTP 503, with a short backoff, and only when the bridge duplicate check says the comment is absent. Retcode 10016, 10019 (no money), market closed, and any `ok: false` with a retcode are terminal. They are not retried.

Reconnect sits in the bridge process, not in the order call. The order call sees a live `/health` or it records `FAILED` and moves on.

The PC connector path already has `expire_stale_commands`. Give those commands the same idempotency key so a re-pull cannot double-submit. That is part of the safety PR, and it helps the single account before the second terminal exists.

## API and the app

New reads, all scoped by the caller's `userId`:

- `GET /api/accounts` — existing list, plus the new fields the UI needs (no secrets). Include `equity`, `balance`, `positions_count`, `day_pl` from the last successful bridge poll, with `as_of` so a stale poll is visible.
- `GET /api/accounts/{id}/positions` — that terminal's positions, magic-filtered.
- `GET /api/accounts/{id}/pnl` — day and open P&L for that login.
- `GET /api/accounts/summary` — the five rows, for a home strip. One slow bridge must not block the other four; the summary endpoint budgets a short timeout per slot and returns `connected: false` for the slow one.
- `POST /api/accounts/{id}/kill` — sets `kill_switch` on that row. The global admin kill switch is unchanged and still stops every slot.

`GET /api/positions/live` keeps working for the **viewed** account (`is_active`) so the current positions screen does not break. It gains an optional `account_id` query. The home equity number follows the same rule.

Polling in `usePolling` stays. The positions screen polls the viewed account. A later UI pass can show five cards; that layout belongs with the visual PR. This work supplies the data.

The account switcher adds "trading on/off" per row, which it already posts to `/api/accounts/{id}/trading`. After the fan-out PR, turning trading on for two rows means both receive the next signal. The reload-the-whole-app behaviour goes away once lists are keyed by account id.

## Migration from the single account

The live login must keep trading through the migration. Each step is safe to stop after.

1. **Schema and one backfill row.** Insert a `trading_accounts` document for the owner if none exists: label from the current terminal's `/account` (login, server, balance), `mode: vps_bridge`, `trading_enabled: true`, `bridge_slot: 1`, `magic: 20260914`, `bridge_url` empty. Execution still reads `settings.bridge_url`. No behaviour change. Apply the Supabase `CREATE TABLE` by hand first, or this insert 503s.
2. **Safety PR on the one account.** Fail closed, idempotency row, duplicate check on the bridge, ledger write that is allowed to block the send. Still one terminal. This is the highest-value change that does not need a second login.
3. **Supervisor and a second terminal in shadow.** Install portable MT5 #2, bridge on 8701, health only. Cloud polls it and shows equity. `trading_enabled` stays false. Port 8700 is not restarted.
4. **Enable trading on account 2 for one strategy on a demo login**, if the owner has a demo. Watch `account_orders` for a session. Then enable the real second login.
5. **Point sizing at the per-account fields.** Until the owner fills them, the user-level $50 (and the strategy override) still apply, so existing gold and FX risk does not jump.
6. **Retire the implicit bridge.** When every trading account has a `bridge_url`, `execute_signal` stops calling the global URL. Keep the env var as the fallback for slot 1 until that day.

Rollback at steps 3–4 is `trading_enabled: false` on the new row, or stopping that bridge process. Slot 1 is untouched.

`auto-deploy.sh` restarts the bridge when `vps-bridge/` changes on `main`. The bridge PR has to be backward compatible: old cloud + new bridge, and new cloud + old bridge, must still place the current account's order. New JSON fields on `/execute` are optional. The cloud sends them only when the account row has a slot.

## Phased pull requests

Each phase is one reviewable PR into `main`, merged only when the owner wants it deployed. Risk is about production trading, not about how large the diff is.

| Phase | PR | Risk | What it changes |
| --- | --- | --- | --- |
| 1 | This PR | Low | Audit, this design, test fixes, `httpx`, pytest on pull requests. No order behaviour change. |
| 2 | Execution safety, one account | Medium | Fail closed when the kill switch cannot be read, and when a provisioned account registry cannot be read. A missing `trading_accounts` table keeps today's single-bridge send, so the live login is not stopped before the SQL exists. Ledger row in `exec_events` (table already in Supabase) written before send. Bridge duplicate check on comment + magic. Retry only after an "already filled?" check comes back empty. Fix `live_loop` to call `_users_cached`. Leave `seed_if_empty` uncalled. Do not change `lifecycle_of` or `STRATEGY_CLASSES`. |
| 3 | Account document and SQL | Low–medium | New fields, cap of 5, Supabase DDL for the owner to run, backfill slot 1. Execution routing unchanged. |
| 4 | Five processes, shadow | High operational, low trading | Portable terminals, supervisor, `initialize(path=...)`, health login check. Slots 2–5 are polled and cannot send. Slot 1 process restart is the risky moment; do it when the market is quiet, and keep the old bridge command one SSH session away. |
| 5 | Fan-out | High | Concurrent per-account orders, per-account sizing, per-account locks, summary status on the signal. Ship behind `trading_enabled` so only slot 1 is on until the owner flips slot 2. |
| 6 | Per-account P&L in the API and the app | Medium | Positions, day P&L, summary. View account vs trading flags. Coordinate with the UI PR so screen layout stays theirs and data hooks stay here. |
| 7 | Security hardening | Medium–high | Separate on purpose. Require a real `FOREXMIND_JWT_SECRET` outside dev. New passwords use a slow hash; old SHA-256 still verifies, then gets rehashed on login. CORS allow-list. Webhook 401 on a bad secret. Bridge listens on localhost; the API reaches it over a private path. Fix `setup.sh` so the probe uses the real token. Scope `/api/agent/activity` to the caller. Rehearse on the VPS before it is on `main`, because a bad secret locks the owner out and auto-deploy will not roll back by itself. |

Phase 3 is stacked on phase 2. Phase 5 waits on 2, 3, and 4. Phase 6 can start from 3 (the reads) and finishes after 5. Phase 7 can happen any time and should not be bundled into a bridge restart.

## Decisions (2026-11-11)

The owner answered the product questions. Items marked **default, owner can change** were not answered directly. They are the choice this build will follow until the owner says otherwise.

| Topic | Decision |
| --- | --- |
| Risk | Each account has its own dollar risk (`risk_per_trade_usd`). A strategy's own `risk_usd` still overrides that number inside the account. While the field is empty, the existing user-level figure is used so today's live lot size does not change. |
| Broker | All five accounts are at the same broker. Symbol names match. `symbol_suffix` is still stored and defaults to none. |
| Where they run | All five terminals run on the current VPS. |
| Who owns them | All five belong to the owner. VPS trading stays owner-only. |
| Strategies | Each account has its own pick-list. A new account starts with none selected. |
| Demo and live | **Default, owner can change.** A demo slot and a live slot may both be `trading_enabled`. One slot's reject does not cancel the other. |
| Drawdown | **Default, owner can change.** Each account has its own drawdown cap (`max_daily_loss_usd`, empty means no extra cap). An optional combined gold cap (`combined_gold_cap_usd` on the book settings) exists and is off when unset. |
| Passwords | VPS env file per slot, `mt5.login` at process start. Not in the database and not in the app. |
| Port 8700 | The current login keeps trading on port 8700. Adding another slot does not restart that bridge. |
| Cap | Five accounts is a hard ceiling. |
| Switcher | Tapping an account only changes the view. Every row with `trading_enabled` still receives signals once fan-out is on. |
| Telegram | One message per signal. The body lists each account's result. |
| Kill switch | A global kill switch, plus one per account. Emergency-close is per account (closing every login because one account hit its limit is the wrong blast radius). The global emergency path remains for a real stop-everything. |
| Netlify / Render | Still unanswered. Do not change the proxy targets until the owner says which host the live site uses. |
