# FOREXMIND Strategy Promotion Doctrine

Owner spec 2026-11-02 — the rules that protect proven strategies.

## The principle

```
One Strategy -> One Protected Python Module -> Standard Signal
             -> Risk Validation -> Trade Management -> Execution
```

- The strategy module is the SINGLE SOURCE OF TRUTH for its trading rules.
- No AI component, integration layer, or research process may silently
  change a proven strategy. The research brain NEVER edits a LIVE module.
- An experimental improvement is always a NEW module (or a new immutable
  parameter version via learning/versions.py) — never an overwrite.

## Module layout (one strategy = one module)

| Strategy (registry id)            | Module                                            |
|-----------------------------------|---------------------------------------------------|
| strategy_h1_trend_breakout        | strategies/gold_specs/h1_trend_breakout.py        |
| strategy_gold_orb                 | strategies/gold_specs/gold_orb.py                 |
| strategy_tpc15_pullback           | strategies/tpc15/strategy.py (adapter: live.py)   |
| strategy_trendbreak_h1            | strategies/research_only/trendbreak.py (adapter: live.py) |
| strategy_zonesweep_m15            | strategies/research_only/zonesweep.py             |
| strategy_trendbreak_v2            | strategies/research_only/trendbreak_v2.py         |
| strategy_hybrid_crt_tbs           | strategies/research_only/hybrid_crt_tbs.py        |
| strategy_fade_crt_ext             | strategies/research_only/fade_crt_extension.py    |

Dormant historical modules (unregistered, kept so old signals/journal keep
their meaning — they can never run): strategies/strategy_1_vp_pivots/,
strategy_1_zero_lag/, strategy_2_ema_atr/, strategy_2_mtf_sweep_bos_retest/,
strategy_2_supply_demand_fvg/.

Research versions of a strategy live beside it or in this directory as a new
module (e.g. `ema_9_21_v2_research.py`) and are registered only when the
owner promotes them.

**New strategies start from the official template:
`strategies/_template_strategy.py`** — copy it, fill in the identity block
and the exact rules, register in `__init__.py`, set the state in
`learning/versions.py`, run the architecture + regression tests.

## Strategy states (owned by learning/versions.py — never by the modules)

| State    | Meaning                                                        | Executes? |
|----------|----------------------------------------------------------------|-----------|
| LIVE     | approved and allowed to generate executable signals            | YES       |
| SHADOW   | real engine over recorded candles, hypothetical trades (research/shadow.py) | NO |
| RESEARCH | experiments, backtests, walk-forward only                      | NO        |
| SLEEPING | monitored/retained for future research, not trading            | NO        |
| RETIRED  | frozen history; no signals, no execution                       | NO        |

Changing a state is an audited data change — it never modifies trading
logic. Every state that is not LIVE is refused server-side
(pretrade.STRATEGY_NOT_LIVE + mt5 SKIPPED_LIFECYCLE).

## Promotion workflow (research -> LIVE)

1. Create a SEPARATE research version (new module or experiment params).
2. Test it on historical data.
3. Walk-forward + out-of-sample where sufficient data exists.
4. Shadow/live observation without risking money (research/shadow.py).
5. Compare against the current approved version (learning.versions.compare_versions).
6. Store the evidence: trades, win rate, average win/loss, expectancy,
   profit factor, max drawdown, performance in R, performance after
   realistic trading costs.
7. Request promotion. ONLY explicit human approval
   (learning.versions.approve_hypothesis) creates the new immutable version.

Never promote on a small sample or one attractive backtest. A research
version never replaces a LIVE strategy automatically.
