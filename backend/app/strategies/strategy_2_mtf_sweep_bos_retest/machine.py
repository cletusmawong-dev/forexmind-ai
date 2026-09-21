"""Strategy 2 (MTF Sweep -> BOS -> Retest) - pure state-machine core.

Faithful translation of the supplied Pine indicator "MTF Sweep -> BOS -> Retest"
(spec sections 4-14, 25, 27, 32). CLOSED candles only; no lookahead; pivots are
confirmed strictly by their right-side bars (Pine pivothigh/pivotlow semantics).

Candle indexing / alignment (documented per spec section 7):
  * Every frame passed here contains CLOSED candles only, oldest first;
    index -1 is the most recently CLOSED candle of that timeframe.
  * Sweep event  : evaluated on the sweep-TF frame. Pine fires on
    newSweepCandle (first tick of a new sweep bar) and inspects bullish/
    bearishSweep[1] - i.e. the candle that has just closed. Here that is
    sweep_df[-1] (previous = sweep_df[-2]). The event is processed once per
    sweep-candle timestamp (state["last_sweep_ts"]).
  * BOS event    : evaluated on the BOS-TF frame. Pivots with swing_len bars
    on each side are confirmed once center+swing_len has closed (a pivot
    centered on bar c is usable from bar c+swing_len inclusive - on that bar
    TradingView's pivothigh returns the value, and the BOS comparison uses
    it). The BOS candle is bos_df[-1]; each BOS-TF candle is processed once
    (state["last_bos_ts"]). broken_high/low is the structure that existed
    BEFORE this candle (prev confirmed value), bos_candle_low/high is the
    breaking candle's own low/high.
  * Retest event : evaluated on the entry-TF frame. The retest candle is
    entry_df[-1] when its timestamp is new (state["last_retest_ts"] - one
    evaluation per bar, exactly like bar-by-bar Pine execution). A failed
    retest does NOT clear the waiting flags (state persists per spec 9).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Spec section 6 - exact mapping. Repo timeframe names ("1D" = Daily).
TIMEFRAME_MAP: Dict[str, Tuple[str, str]] = {
    "5M": ("1H", "15M"),   # entry -> (sweep_tf, bos_tf)
    "15M": ("4H", "1H"),
    "30M": ("4H", "1H"),
    "1H": ("1D", "4H"),
    "4H": ("1D", "4H"),
}

MACHINE_STAGES = ("NO_SWEEP", "SWEEP_DETECTED", "WAITING_FOR_BOS",
                  "BOS_CONFIRMED", "WAITING_FOR_RETEST", "SIGNAL")

# Honest minimums: entry needs ATR warmup history; a sweep needs exactly its
# closed pair; BOS needs the pivot machinery (2*swing + 5 at swing=3). Live
# scans feed ~400-bar frames; these floors only reject genuinely starved data.
_MIN_BARS = {"entry": 200, "sweep": 2, "bos": 11}


def default_state() -> Dict[str, Any]:
    """Spec section 18 - persisted machine state (per market + entry TF)."""
    return {
        "bullish_setup": False,
        "bearish_setup": False,
        "structure_high": None,
        "structure_low": None,
        "broken_high": None,
        "broken_low": None,
        "bos_candle_low": None,
        "bos_candle_high": None,
        "waiting_bull_retest": False,
        "waiting_bear_retest": False,
        "last_sweep_ts": None,
        "last_bos_ts": None,
        "last_retest_ts": None,
        "last_signal_key": None,
    }


# ---------------------------------------------------------------------------
# data integrity (spec section 25)
# ---------------------------------------------------------------------------
def integrity_check(df, name: str, min_bars: int) -> Optional[str]:
    """Returns an error string when the frame must NOT be traded on."""
    if df is None or len(df) < min_bars:
        return f"{name}: insufficient candles ({0 if df is None else len(df)} < {min_bars})"
    ts = [int(x.timestamp()) for x in df.index]
    if any(b <= a for a, b in zip(ts, ts[1:])):
        return f"{name}: timestamps not strictly increasing (duplicate/out-of-order candle)"
    return None


# ---------------------------------------------------------------------------
# pivots (spec section 10) - Pine ta.pivothigh / ta.pivotlow semantics
# ---------------------------------------------------------------------------
def confirmed_pivots(df, length: int) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Confirmed swing pivots of a CLOSED-candle frame.

    A pivot high centered at i requires df.high[i] strictly greater than the
    `length` highs on each side; it is CONFIRMED once bar i+length has closed
    (i + length <= last index). Returns ([highs], [lows]) as (center_idx, price).
    """
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    n = len(df)
    highs: List[Tuple[int, float]] = []
    lows: List[Tuple[int, float]] = []
    for i in range(length, n - length):
        window_h = h[i - length:i + length + 1]
        if h[i] == window_h.max() and (window_h > h[i]).sum() == 0:
            highs.append((i, float(h[i])))
        window_l = l[i - length:i + length + 1]
        if l[i] == window_l.min() and (window_l < l[i]).sum() == 0:
            lows.append((i, float(l[i])))
    return highs, lows


def latest_structure(df, length: int) -> Tuple[Optional[float], Optional[float]]:
    """(structure_high, structure_low) confirmed as of the last closed bar."""
    highs, lows = confirmed_pivots(df, length)
    return (highs[-1][1] if highs else None, lows[-1][1] if lows else None)


# ---------------------------------------------------------------------------
# sweep (spec section 8)
# ---------------------------------------------------------------------------
def detect_sweep(sweep_df) -> Optional[str]:
    """'bull' | 'bear' on the last two CLOSED sweep candles; None otherwise."""
    if sweep_df is None or len(sweep_df) < 2:
        return None
    prev_o = float(sweep_df["open"].iloc[-2]); prev_c = float(sweep_df["close"].iloc[-2])
    cur_o = float(sweep_df["open"].iloc[-1]); cur_c = float(sweep_df["close"].iloc[-1])
    cur_l = float(sweep_df["low"].iloc[-1]); cur_h = float(sweep_df["high"].iloc[-1])
    prev_l = float(sweep_df["low"].iloc[-2]); prev_h = float(sweep_df["high"].iloc[-2])

    if prev_c < prev_o and cur_c > cur_o and cur_l < prev_l:   # bullish sweep
        return "bull"
    if prev_c > prev_o and cur_c < cur_o and cur_h > prev_h:   # bearish sweep
        return "bear"
    return None


# ---------------------------------------------------------------------------
# BOS (spec sections 10-11)
# ---------------------------------------------------------------------------
def detect_bos(bos_df, state: Dict[str, Any], swing_len: int,
               direction: str) -> Tuple[bool, Dict[str, Any]]:
    """Evaluate a BOS on the last CLOSED BOS-TF candle for `direction`.

    Structures are updated with pivots confirmed AS OF this candle close
    (center + swing_len <= last index), matching TradingView execution order;
    the break is then compared against the structure that existed BEFORE the
    breaking candle (the stored value is only replaced after the check when
    the new pivot center is not the value being broken - see below).
    Returns (bos_happened, updated_state).
    """
    st = dict(state)
    if bos_df is None or len(bos_df) < 2 * swing_len + 5:
        return False, st
    highs, lows = confirmed_pivots(bos_df, swing_len)
    # structures as of the close of the BOS candle
    st["structure_high"] = highs[-1][1] if highs else st.get("structure_high")
    st["structure_low"] = lows[-1][1] if lows else st.get("structure_low")

    close = float(bos_df["close"].iloc[-1])
    if direction == "bull":
        if not st.get("bullish_setup") or st.get("structure_high") is None:
            return False, st
        # the structure broken must pre-date the BOS candle (a pivot whose
        # center is the last bars cannot be 'broken' by itself)
        prior_highs = [p for p in highs if p[0] <= len(bos_df) - 2 - swing_len]
        broken = prior_highs[-1][1] if prior_highs else st.get("structure_high")
        if broken is None or close <= float(broken):
            return False, st
        st["broken_high"] = float(broken)
        st["bos_candle_low"] = float(bos_df["low"].iloc[-1])
        st["waiting_bull_retest"] = True
        st["waiting_bear_retest"] = False
        return True, st
    else:
        if not st.get("bearish_setup") or st.get("structure_low") is None:
            return False, st
        prior_lows = [p for p in lows if p[0] <= len(bos_df) - 2 - swing_len]
        broken = prior_lows[-1][1] if prior_lows else st.get("structure_low")
        if broken is None or close >= float(broken):
            return False, st
        st["broken_low"] = float(broken)
        st["bos_candle_high"] = float(bos_df["high"].iloc[-1])
        st["waiting_bear_retest"] = True
        st["waiting_bull_retest"] = False
        return True, st


# ---------------------------------------------------------------------------
# retest (spec sections 12-13)
# ---------------------------------------------------------------------------
def check_retest(entry_df, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Retest confirmation on the last CLOSED entry-TF candle."""
    if entry_df is None or len(entry_df) < 2:
        return None
    if state.get("waiting_bull_retest") and state.get("broken_high") is not None:
        low = float(entry_df["low"].iloc[-1]); close = float(entry_df["close"].iloc[-1])
        if low <= float(state["broken_high"]) < close:
            return {"direction": "BUY", "entry": close,
                    "sl": float(state["bos_candle_low"]),
                    "retest_ts": int(entry_df.index[-1].timestamp())}
    if state.get("waiting_bear_retest") and state.get("broken_low") is not None:
        high = float(entry_df["high"].iloc[-1]); close = float(entry_df["close"].iloc[-1])
        if high >= float(state["broken_low"]) > close:
            return {"direction": "SELL", "entry": close,
                    "sl": float(state["bos_candle_high"]),
                    "retest_ts": int(entry_df.index[-1].timestamp())}
    return None


def stage_of(state: Dict[str, Any]) -> str:
    if state.get("waiting_bull_retest") or state.get("waiting_bear_retest"):
        return "WAITING_FOR_RETEST"
    if state.get("bullish_setup") or state.get("bearish_setup"):
        return "WAITING_FOR_BOS"
    return "NO_SWEEP"
