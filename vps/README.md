# VPS side — Windows MT5 + ForexMind bridge

This directory holds deployment preparation for the (future) Windows VPS.
The VPS has NOT been purchased yet; nothing here should be deployed.

## Components (when the day comes)

1. **MetaTrader 5** — broker build, Exness DEMO account first.
2. **ForexMind bridge** — small Windows-side HTTP service next to MT5.

## Bridge contract (unchanged, backward compatible)

- `GET /account` -> account info incl. `balance` (auth: `X-Bridge-Token` header)
- `POST /execute` -> `{signal_id, symbol, direction, lots, sl, tp, magic}`
  -> `{ok, price, volume, ticket}` on success; `{error}` on failure.
- `magic` is always `20260914` for ForexMind orders.

The cloud backend (`app/execution/mt5.py`) is already this bridge's client —
it checks `GET /account` for the live balance, computes the risk-based lot
(1% cap, 0.01 step, per-symbol pip tables) and posts the order. If the bridge
is unreachable the order is NOT sent and the signal degrades to advisory
with an honest log (`SKIPPED_BRIDGE_OFFLINE`).

## Manual PC connector (alternative transport)

Runs on the user's own Windows PC, DIALS OUT to the ForexMind API (no router
port forwarding): registers with the pairing code from Settings, polls queued
commands from `exec_commands`, executes locally, acknowledges fills, pushes
deal history. Commands expire after 15 minutes offline.

## Security rules

- Bridge token lives ONLY in env vars (`MT5_BRIDGE_TOKEN`) on both sides.
- Never expose the bridge to the public internet without the token header.
- Execution stays OFF (`EXECUTION_MODE=off`, kill switch true) until the
  user explicitly enables it after the demo-account verification checklist
  in `docs/VPS_MIGRATION.md`.
