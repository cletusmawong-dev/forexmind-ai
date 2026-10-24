"""RESEARCH CANDIDATES - NOT REGISTERED, NOT LIVE (owner spec 2026-10-24).

Two owner-provided research candidates, entry/exit rules only:
  * trendbreak.py - "TrendBreak": H1 breakout in the H4 trend direction.
  * zonesweep.py  - "ZoneSweep": M15 supply/demand zone after BOS + liquidity
                    sweep, with H1+H4 trend alignment.

Isolation contract (identical to TPC-15, and per the spec's own status line
"research candidates ... Not live"):
  * NOT in app.strategies.STRATEGY_CLASSES - never scanned, never signalled,
    never executed by the live loop.
  * Pure modules: pandas/stdlib only; no execution, broker, store or notify
    imports. Sizing/account risk is deliberately absent - the app layer owns
    that if the owner ever promotes a candidate.
  * No instruments hardcoded beyond the spec's own market/cost tables; the
    spec explicitly EXCLUDES XAGUSD ("lost in testing").
  * Closed bars only at decision time; higher-timeframe context is read
    as-of the decision bar (an H1/M15 bar only sees higher-TF bars that
    closed at or before its own close - no lookahead, no repainting).
  * The spec's backtest reference numbers are the OWNER's historical
    results, quoted in reports as document claims - never presented as our
    measured performance. Do NOT re-optimise the parameters (builder note).
"""
