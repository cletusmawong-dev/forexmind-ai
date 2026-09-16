# ForexMind AI — VPS Migration Guide

Status: **PREPARATION ONLY**. The VPS has NOT been purchased yet. This
document is the runbook for the day it is. Execution stays **OFF** until the
user explicitly enables it — the VPS migration itself must never activate
trading.

---

## 1. Current architecture

- **Frontend**: React + Vite + Tailwind, mobile-first PWA (Netlify).
- **Backend**: Python/FastAPI, single process; background agent loop does
  tracking, scanning, learning (Render today, Docker-ready now).
- **Database**: Firestore (via a Firestore-shaped store; local JSON in dev).
- **Market data**: TwelveData REST candles (closed-candle model; Yahoo +
  OANDA fallbacks). No tick stream exists today — reported honestly.
- **AI**: XKiro (mistral-large-2512). **Notifications**: Telegram.
- **Execution**: `app/execution/mt5.py` — modes `off | vps | manual`,
  magic `20260914`, caps 6/day + 1%/trade + TP2, 15-min expiry, kill switch.

## 2. Target VPS architecture

```
                MARKET DATA (provider / stream)
                         |
                         v
              +----------------------+
              | FOREXMIND VPS        |
              |  api (FastAPI image) |   <- this repo's Dockerfile
              |  tick collector      |   <- future (interfaces ready)
              |  candle builder      |   <- app/market_data/candle_builder.py
              |  integrity/gaps      |   <- app/market_data/integrity.py
              |  strategy engine     |   <- unchanged, closed candles only
              |  MT5 bridge client   |   <- app/execution/mt5.py (unchanged)
              +----------+-----------+
                         v
                  MT5 TERMINAL (Windows)
                         v
                    BROKER ACCOUNT
```

The mobile frontend keeps talking to the normal ForexMind API. It NEVER
talks to MT5 directly.

## 3. Required VPS specifications

- Windows Server 2022 (MT5 is a Windows app), 2+ vCPU, 4+ GB RAM, 40+ GB SSD.
- Same region as the broker's trade server (latency matters for execution).
- Docker Desktop OR plain Python 3.12 (api can run bare); MT5 runs natively.

## 4. Required software

- Docker (or Python 3.12), MetaTrader 5 (broker build), the ForexMind bridge
  on the Windows side, Git.

## 5. Environment variables

Copy `backend/.env.example` to `backend/.env` and fill in. Secrets NEVER go
into git. Execution-related defaults are safe: `EXECUTION_MODE=off`,
`EXECUTION_KILL_SWITCH=true`.

## 6. Market-data provider configuration

- `MARKET_DATA_PROVIDER=live` + `TWELVEDATA_API_KEY=...` (current REST
  candles). Optional `OANDA_API_TOKEN` for broker-grade candles.
- Future stream: `MARKET_DATA_STREAM_URL` / `MARKET_DATA_STREAM_API_KEY`.
  While unset, `/api/system/status` honestly reports
  `stream.connected = false`. No streaming is simulated.

## 7-8. MT5 + bridge installation (Windows side)

1. Install the broker's MT5, log in to the **Exness DEMO** account first.
2. Run the ForexMind bridge beside MT5 (it exposes only `GET /account` and
   `POST /execute`, authenticated by the bridge token header).
3. Set `MT5_BRIDGE_URL` + `MT5_BRIDGE_TOKEN` in the api `.env`.
4. Verify: `curl $MT5_BRIDGE_URL/account -H "X-Bridge-Token: ..."` returns
   the account JSON.

## 9. Startup process

- API: `docker compose up -d --build` on the VPS (or `uvicorn app.main:app`).
- Health: `GET /api/health` (app), `GET /api/system/status` (full honest view).

## 10. Health checks

`/api/system/status` reports, per section, `configured` vs `connected`:

- `market_data`: provider, capabilities, per-market last candle + staleness.
- `vps_bridge`: configured? reachable (live `/account` probe)?
- `execution`: mode (off/vps/manual), kill switch, caps.

**Configured != connected.** Nothing may show "connected" from config alone.

## 11. Data verification

Compare old and new data paths on the same symbols/timeframes: timestamps,
OHLC, EMA9/EMA21/ATR14/HTF-100-EMA (all deterministic — see
`tests/test_indicator_determinism.py`), crossover timestamps, and generated
signals. The VPS path must be equivalent or better BEFORE activation.

## 12. Migration checklist (do not skip steps)

1. Purchase VPS (Windows).
2. Install Docker or Python 3.12.
3. Deploy the api image; set `.env`.
4. Configure the market-data provider; verify candles.
5. Start/verify candle storage; check `/api/system/status` markets.
6. Compare EMA values vs the known fixture (`test_indicator_determinism.py`).
7. Verify Strategy 1 and Strategy 2 signal behavior on the VPS feed vs the
   old feed (same fixture period).
8. Install MT5 (Exness DEMO) + bridge; set bridge env vars.
9. Verify `/account` through the bridge.
10. Test one execution **on demo** with mode `vps` + kill switch off.
11. Verify fills/deal reconciliation (`test_execution.py` flows).
12. **Leave `EXECUTION_MODE=off` until the user explicitly approves.**

## 13. Rollback plan

- Point the frontend/DNS back at the Render backend (unchanged app).
- Execution: flip mode `off` (Settings or `EXECUTION_MODE=off`) — signals
  continue; open MT5 positions are managed manually on the broker.
- No database migration is required to roll back: Firestore stays the store.

## 14. Execution activation procedure (manual, user-only)

1. User verifies the demo-account checklist above.
2. User sets `EXECUTION_KILL_SWITCH=false` (or enables per-user in Settings).
3. User flips the mode to `vps` (Settings) — only possible when the bridge
   env is configured (`set_mode` refuses otherwise).
4. Caps stay: 6/day, 1%/trade, TP2, 15-min expiry, magic 20260914.
5. The AI can NEVER do steps 2-4 itself.

---

## Local development (unchanged)

- Backend: `cd backend && uvicorn app.main:app --reload` (or
  `docker compose up --build`).
- Frontend: `cd frontend && npm run dev`.
- Tests: `cd backend && python -m pytest -q`.
- No VPS, no bridge, no broker needed for any of the above.
