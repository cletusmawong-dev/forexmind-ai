# ForexMind AI

**Your Personal AI Trading Research Agent**

A mobile-first AI trading **research & signal** platform. ForexMind AI analyzes markets,
detects setups using exactly two predefined strategies, explains why each setup qualifies,
tracks outcomes, learns from history, runs scientific one-variable experiments — and **never**
executes trades. You stay in control: the user manually enters trades on MT5.

> Live in this sandbox: **Web app preview on port 5173** (React PWA) → FastAPI backend on port 8000.
> Demo login: `demo@forexmind.ai` / `demo1234` (or the "Continue with demo account" button).
>
> ⏱ All market data in this sandbox is a labeled **DEMO / HISTORICAL replay** of stored,
> realistic-but-synthetic datasets. It is never presented as live data (SPEC §37/§59).

---

## What's inside

```
forexmind-ai/
├── backend/                     Python + FastAPI
│   ├── app/
│   │   ├── strategies/          Strategy modules (§8, §47-48)
│   │   │   ├── base.py                    Strategy interface + shared simulator
│   │   │   ├── strategy_1_zero_lag/       AlgoAlpha Zero Lag Trend Signals (MTF)
│   │   │   └── strategy_2_ema_atr/        KN Smart TP/SL (9/21 EMA)
│   │   ├── engine/              Signal engine (guards, dedup) + lifecycle tracker
│   │   ├── agent/               Objective system, activity log, AI provider, chat
│   │   ├── learning/            Lessons, hypotheses, experiments, metrics, versions
│   │   ├── backtesting/         Walk-forward backtester (same-dataset comparisons)
│   │   ├── market_data/         MarketDataProvider abstraction + HistoricalDemoProvider
│   │   ├── db/                  Firestore-shaped DataStore (local now, Firebase-ready)
│   │   ├── notifications/       In-app records + FCM adapter (activates with creds)
│   │   ├── core/                Indicators (Pine-faithful), security
│   │   └── api/                 All REST endpoints (§44)
│   ├── tests/                   43 automated tests (§58) — all passing
│   ├── data/demo/               Generated DEMO datasets (5 instruments × 180 days of 5M)
│   └── seed/                    Deterministic dataset generator
├── frontend/                    React + Vite + TypeScript PWA (mobile-first)
│   └── src/screens/             Home, Signals, Signal detail, Agent (+chat),
│                                 Learning Lab, Strategies, Journal, Analytics,
│                                 Notifications, Settings, Login, Splash
└── README.md
```

## Design language (v2 — Liquid Glass)

The UI follows an **iOS-17-style liquid glass + premium trading terminal** direction:

- three depths of glass (primary / secondary / floating) with translucency, blur, hairline
  borders and inset highlights — never plastic
- a typography-led hierarchy instead of tiles: large light-weight numerals, quiet eyebrows,
  hairline dividers, generous whitespace
- sophisticated graphite base with barely-there blue/violet/cyan ambient lighting + film grain
- refined accents: electric blue `#6C9EFF`, cyan `#7CD5F2`, violet `#A79BF7`, soft green/red/amber
- floating pill glass navigation on mobile, sidebar on desktop (responsive, not stretched)
- quiet motion: animated numbers, breathing AI orb, glowing timeline nodes, pull-to-refresh,
  expandable analysis sections, subtle press feedback — "quiet luxury", never a gaming interface

## The two strategies (exactly as specified)

**Strategy 1 — AlgoAlpha Zero Lag Trend Signals (MTF)** (`length=70`, `band_mult=1.2`)
- `lag = (length-1)//2` → `zlema = EMA(src + (src - src[lag]), length)`
- `volatility = highest(ATR(length), length) × 3 × 1.2`
- Trend state: `+1` after close crosses above `zlema + vol`, `-1` after crossing below `zlema - vol`
- **Small-arrow entries only:** close crosses the zlema *in the direction of an already
  established trend* (`trend == previous trend == ±1`) — never the raw trend-change bar
- MTF trend displayed across 5M / 15M / 1H / 4H / 1D

**Strategy 2 — KN Smart TP/SL Signals** (the one requested change: **9/21**, not 5/13)
- BUY when EMA(9) crosses above EMA(21); SELL on the cross below
- `SL = ATR(14) × 1.5` · `TP1/TP2/TP3 = 1R / 2R / 3R`
- Lifecycle tracking of TP1 → TP2 → TP3 / SL with conservative intrabar rules
  (SL evaluated first; one TP banks per candle)

## The scientific learning loop

`TRADE → RESULT → ANALYSIS → LESSON → HYPOTHESIS → ONE-VARIABLE TEST → RESULT → USER APPROVAL → NEW VERSION`

- Lessons are **observations with evidence counts** — never conclusions without data (§19-§20)
- Hypotheses change exactly **ONE** variable (e.g. Strategy 2 `fast_len 9 → 10`)
- Experiments run original vs experimental **on the same dataset** (§39)
- Verdicts: `IMPROVED / NO SIGNIFICANT CHANGE / WORSE / INSUFFICIENT DATA` (§23)
- **Hard backend validation:** changing two variables (e.g. `fast_len 9→10` AND `atr_len 14→15`)
  is rejected with HTTP 422 — *"Experiment rejected: more than one strategy variable was changed."* (§28)
- Only **user approval** creates a new version (v1.0 → v1.1 …) (§26, §41)
- Full version history: view / compare / rollback — history is never destroyed (§27, §52-53)

## Absolute rules honored

No auto-trading · no forced signals from the objective ("No qualifying setup. Capital protected.")
· no automatic strategy changes · every signal explains why it qualified · single losses never
change a strategy · no fabricated data, backtests or AI analysis · XKiro key & Firebase secrets
stay server-side · demo data always labeled.

## Running locally

```bash
# backend
cd backend && pip install -r requirements.txt
python3 ../seed/generate_data.py   # once: creates demo datasets
uvicorn app.main:app --host 0.0.0.0 --port 8000

# frontend (separate shell)
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api)
```

Environment switches (all optional — the sandbox runs fully without them):

| Variable | Effect |
|---|---|
| `FIREBASE_PROJECT_ID` (+ `GOOGLE_APPLICATION_CREDENTIALS` or `FIREBASE_CREDENTIALS_JSON`) | Activates live Firestore via firebase-admin (`pip install firebase-admin`) |
| `XKIRO_API_KEY` (+ `XKIRO_BASE_URL`, `XKIRO_MODEL`) | Activates the XKiro provider for analysis/chat (key never leaves the server) |
| `FCM_ENABLED=1` + Firebase creds | Push notifications via Firebase Cloud Messaging |
| `MARKET_DATA_PROVIDER` | Swaps the market-data provider (implement `MarketDataProvider`) |
| `REPLAY_INTERVAL_SEC` | Speed of the historical replay (default 4s per 5M candle) |

## Key API endpoints (§44)

`POST /api/signals/analyze` · `GET /api/signals[/{id}]` · `POST /api/signals/{id}/action`
· `GET /api/agent/status|activity` · `POST /api/chat` · `GET /api/lessons`
· `GET /api/hypotheses` · `POST /api/hypotheses/{id}/approve|reject` · `GET|POST /api/experiments`
· `GET /api/strategies[/{id}/versions]` · `POST /api/strategies/{id}/rollback`
· `GET /api/journal/stats` · `GET /api/analytics/summary` · `GET /api/system/info`

## Tests (§58) — 43 passing

Strategy 1 & 2 detection math · EMA 9/21 cross · ATR · SL/TP calculation · signal lifecycle
(TP ladder, conservative SL-first) · signal-vs-user result separation · win/loss analysis ·
hypothesis creation · **one-variable restriction (incl. the mandated two-variable attack test)**
· approval → version creation · rejection · rollback with history retention · auth ·
user-data scoping · grounded chat (no fabrication) · honest "no setup" analysis.

## Next steps (plugging in the real world)

1. **Firebase** — share a project ID + service-account JSON → set the two env vars → the
   Firestore adapter activates with zero application changes (same collections: users,
   signals, trades, strategies, strategy_versions, lessons, hypotheses, experiments, …).
2. **XKiro** — share the API docs/endpoint shape → I'll finish `XKiroProvider` against the real
   schema. Until then the built-in grounded analyst powers analysis and chat honestly.
3. **Live market data** — implement `MarketDataProvider` against your feed; strategies, signals,
   journal and learning all consume candles through the interface and remain untouched.
4. **FCM push** — add server credentials to fan notifications out to devices.
