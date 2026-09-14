"""MT5 execution (VPS bridge).

The cloud backend never talks to MT5 directly - MT5's terminal is Windows-only.
Instead a tiny bridge service runs on the user's Windows VPS next to the MT5
terminal and exposes a token-protected HTTP API:

    GET  /health          terminal + account snapshot
    GET  /account         balance / equity / currency
    POST /execute         market order  {signal_id, symbol, direction, lots, sl, tp}
    GET  /positions       open positions
    POST /close           {ticket}
    GET  /deals?since=    deal history for W/L confirmation

Execution policy (user decision, 2026-09-14):
  * fully automatic on every qualifying signal (EXECUTION_MODE=mt5_bridge),
  * demo account first - never real money while unattended,
  * hard safety rails: kill switch, max trades/day, risk % cap per trade,
  * every failure degrades to "signal only" - execution never blocks the agent.

Real W/L: once the bridge is live, trade results are confirmed from actual
MT5 deal history (fills, spread, swap) instead of simulated candle tracking.
"""
