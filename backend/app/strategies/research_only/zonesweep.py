"""Strategy 2 (research candidate): "ZoneSweep" - M15 supply/demand zone
after BOS and liquidity sweep, with H1+H4 trend. Owner spec 2026-10-24,
implemented verbatim. Entry/exit rules only - no sizing, no account risk.

Spec (verbatim source of truth):
  Markets EURUSD, GBPUSD, USDJPY, XAUUSD (XAGUSD excluded - too few trades
  to judge). M15 entries, H1 and H4 context. ATR(14) on M15 (simple rolling
  mean of TR). H1/H4 trend per the TrendBreak definition, read as-of the
  M15 bar's close from completed higher-TF bars only.
  Swings: fractal L=5 - bar j is a swing HIGH if its high is the STRICT
  maximum of the 5 bars before it and the highest among the 5 bars after
  it; KNOWN only 5 bars later (j is usable from bar j+5). Swing LOW mirror.
  Bullish zone at M15 close of bar i:
    1. latest confirmed swing LOW formed AFTER the latest confirmed swing
       HIGH (a pullback happened);
    2. close[i] above that swing high = bullish BOS; each swing high
       triggers only once;
    3. displacement: close[i] - low[swing-low bar] >= 2 x ATR[i];
    4. zone = swing-low candle's LOW (bottom) to its BODY TOP max(open,close)
       (top);
    5. zone height within 0.3 - 2.5 x ATR[i];
    6. sweep flag: that swing low is LOWER than the previous confirmed
       swing low;
    7. H1 trend UP and H4 trend UP at bar i.
    Only zones WITH the sweep flag AND both trends aligned are traded.
  Bearish = exact mirror (zone = swing-high BODY BOTTOM .. HIGH; sweep =
  swing high HIGHER than the previous confirmed swing high; trends DOWN).
  Entry: zone live for 96 M15 bars after bar i. LONG limit at the zone TOP,
  triggered when a later bar's LOW <= zone top; fill at the OPEN if that
  bar opens below the zone top. Cancel (no trade) if before the touch any
  M15 bar CLOSES below the zone bottom. One trade per zone. Skip if the
  risk distance (entry to stop) < 3 x round-trip cost or > 4 x ATR[i].
  Exit: SL = zone bottom - 0.25 x ATR[i] (long; short mirrored), never
  moved. TP = entry +/- 8 x stop distance (1:8). Time exit at the close of
  the 96th M15 bar AFTER THE FILL. If the fill bar itself reaches the stop,
  count a loss; targets are checked from the next bar; a bar touching both
  counts the stop.
  Reference costs (replace with the broker's real spread + slippage):
  EURUSD 0.00012, GBPUSD 0.00016, USDJPY 0.015, XAUUSD 0.35.
  The spec warns: after costs it turns negative if costs roughly double -
  verify real spreads first. Do not re-optimise.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

SPEC = {
    "name": "ZoneSweep",
    "entry_tf": "15M",
    "context_tf": ["1H", "4H"],
    "markets": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
    "excluded_markets": ["XAGUSD"],
    "atr_period": 14,
    "swing_len": 5,
    "displacement_atr": 2.0,
    "zone_height_bounds_atr": (0.3, 2.5),
    "zone_life_bars": 96,
    "time_exit_bars_after_fill": 96,
    "sl_buffer_atr": 0.25,
    "tp_multiple": 8.0,
    "min_risk_cost_multiple": 3.0,
    "max_risk_atr": 4.0,
    "reference_costs": {"EURUSD": 0.00012, "GBPUSD": 0.00016,
                        "USDJPY": 0.015, "XAUUSD": 0.35},
    "reference": ("owner backtest (costs in): win ~19-22%, avg win ~+5R, "
                  "avg loss ~-1.15R, PF ~1.06-1.08, expectancy ~+0.06..0.07R, "
                  "~150-220 trades/yr; longest losing streak 20-28; goes "
                  "negative if real costs are ~2x the assumed ones"),
}

SIGNAL = "ZONE_CREATED"
NO_SIGNAL = "NO_SIGNAL"
INSUFFICIENT = "INSUFFICIENT_DATA"
OUT_OF_SPEC_MARKET = "OUT_OF_SPEC_MARKET"


# ---------------------------------------------------------------------------
# frames, ATR, swings, trends (shared shape with trendbreak.py)
# ---------------------------------------------------------------------------
def _frame(candles, name: str) -> pd.DataFrame:
    if candles is None:
        raise ValueError(f"{name} candle data is missing (None)")
    if isinstance(candles, pd.DataFrame):
        df = candles.copy()
    else:
        df = pd.DataFrame(list(candles))
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"{name} frame missing column '{col}'")
    if isinstance(df.index, pd.DatetimeIndex):
        idx = df.index
    elif "ts" in df.columns:
        idx = pd.to_datetime(df["ts"], unit="s", utc=True)
    elif "time" in df.columns:
        idx = pd.to_datetime(df["time"], utc=True)
    else:
        raise ValueError(f"{name} frame needs a UTC datetime index or ts/time column")
    idx = pd.DatetimeIndex(idx)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df[["open", "high", "low", "close"]].astype(float).sort_index()


def _atr_simple(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _trend_state(hf: pd.DataFrame) -> pd.DataFrame:
    ema50 = hf["close"].ewm(span=50, adjust=False).mean()
    ema200 = hf["close"].ewm(span=200, adjust=False).mean()
    up = (ema50 > ema200) & (hf["close"] > ema200)
    down = (ema50 < ema200) & (hf["close"] < ema200)
    return pd.DataFrame({"trend": up.astype(int) - down.astype(int)})


def _trend_asof(trend_df: pd.DataFrame, t: pd.Timestamp) -> int:
    sub = trend_df.loc[trend_df.index <= t]
    return int(sub["trend"].iloc[-1]) if not sub.empty else 0


def swing_points(df: pd.DataFrame, length: int = SPEC["swing_len"]):
    """Fractal swings. Returns (highs, lows): lists of (bar_index_position,
    price). A swing HIGH at j needs high[j] STRICTLY above the 5 highs before
    it and >= the 5 highs after it; it is CONFIRMED (known) at bar j+length.
    NOTE (documented interpretation): the before-side is strict per the spec;
    the after-side 'highest among' tolerates ties (>=)."""
    n = len(df)
    highs: List[Dict[str, Any]] = []
    lows: List[Dict[str, Any]] = []
    hi, lo = df["high"].values, df["low"].values
    for j in range(length, n - length):
        before_h = hi[j - length:j]
        after_h = hi[j + 1:j + 1 + length]
        if hi[j] > before_h.max() and hi[j] >= after_h.max():
            highs.append({"pos": j, "price": float(hi[j]),
                          "confirmed_at": j + length})
        before_l = lo[j - length:j]
        after_l = lo[j + 1:j + 1 + length]
        if lo[j] < before_l.min() and lo[j] <= after_l.min():
            lows.append({"pos": j, "price": float(lo[j]),
                         "confirmed_at": j + length})
    return highs, lows


def round_trip_cost(market: str) -> Optional[float]:
    return SPEC["reference_costs"].get((market or "").upper())


def check_market(market: Optional[str], cost: Optional[float] = None) -> Optional[str]:
    if market is None:
        return None
    m = market.upper()
    if m in SPEC["excluded_markets"] or m not in SPEC["markets"]:
        return OUT_OF_SPEC_MARKET
    if cost is None and round_trip_cost(m) is None:
        return OUT_OF_SPEC_MARKET          # no cost -> the risk floor cannot run
    return None


# ---------------------------------------------------------------------------
# zone detection engine (stateful stream -> used by evaluate() and backtest())
# ---------------------------------------------------------------------------
class ZoneEngine:
    """Streams closed M15 bars, maintains confirmed swings and emits zones
    at BOS bars exactly per the spec. Pure research helper - no I/O."""

    def __init__(self, cost: Optional[float]):
        self.cost = cost
        self.highs: List[Dict[str, Any]] = []    # confirmed swing highs
        self.lows: List[Dict[str, Any]] = []     # confirmed swing lows
        self.used_highs: set = set()             # swing highs already BOSed
        self.used_lows: set = set()              # swing lows already BOSed

    def _confirm_through(self, df: pd.DataFrame, i: int) -> None:
        """Confirm swings whose 5 right bars completed at bar i (j = i-5)."""
        j = i - SPEC["swing_len"]
        if j < SPEC["swing_len"]:
            return
        hi, lo = df["high"].values, df["low"].values
        if hi[j] > hi[j - SPEC["swing_len"]:j].max() and \
                hi[j] >= hi[j + 1:j + 1 + SPEC["swing_len"]].max():
            self.highs.append({"pos": j, "price": float(hi[j])})
        if lo[j] < lo[j - SPEC["swing_len"]:j].min() and \
                lo[j] <= lo[j + 1:j + 1 + SPEC["swing_len"]].min():
            self.lows.append({"pos": j, "price": float(lo[j])})

    def on_bar(self, df: pd.DataFrame, i: int, atr_i: float,
               trend_h1: int, trend_h4: int) -> Optional[Dict[str, Any]]:
        """Evaluate the M15 close at bar i. Returns a zone dict or None.
        The broken swing is marked used the moment its BOS fires - before
        any quality filtering (spec: each swing triggers only once)."""
        self._confirm_through(df, i)
        c_i = float(df["close"].iloc[i])
        zone = None

        # ---- bullish: BOS above the latest confirmed swing high ----
        if len(self.lows) >= 2 and self.highs:
            low, prev_low = self.lows[-1], self.lows[-2]
            high = self.highs[-1]
            if (low["pos"] > high["pos"]                 # 1. pullback ordering
                    and c_i > high["price"]              # 2. BOS close
                    and high["pos"] not in self.used_highs):
                self.used_highs.add(high["pos"])
                zone = self._build_zone(
                    df, i, atr_i, trend_h1, trend_h4, side="LONG",
                    swing_pos=low["pos"], c_i=c_i,
                    zone_bottom=float(df["low"].iloc[low["pos"]]),
                    zone_top=float(max(df["open"].iloc[low["pos"]],
                                       df["close"].iloc[low["pos"]])),
                    sweep=low["price"] < prev_low["price"])   # 6. liquidity sweep

        # ---- bearish: BOS below the latest confirmed swing low ----
        if zone is None and len(self.highs) >= 2 and self.lows:
            high, prev_high = self.highs[-1], self.highs[-2]
            low = self.lows[-1]
            if (high["pos"] > low["pos"]
                    and c_i < low["price"]
                    and low["pos"] not in self.used_lows):
                self.used_lows.add(low["pos"])
                zone = self._build_zone(
                    df, i, atr_i, trend_h1, trend_h4, side="SHORT",
                    swing_pos=high["pos"], c_i=c_i,
                    zone_top=float(df["high"].iloc[high["pos"]]),
                    zone_bottom=float(min(df["open"].iloc[high["pos"]],
                                          df["close"].iloc[high["pos"]])),
                    sweep=high["price"] > prev_high["price"])
        return zone

    def _build_zone(self, df, i, atr_i, trend_h1, trend_h4, side, swing_pos,
                    c_i, zone_bottom, zone_top, sweep) -> Dict[str, Any]:
        swing_ext = float(df["low"].iloc[swing_pos]) if side == "LONG" \
            else float(df["high"].iloc[swing_pos])
        disp_ok = ((c_i - swing_ext) >= SPEC["displacement_atr"] * atr_i) \
            if side == "LONG" else \
            ((swing_ext - c_i) >= SPEC["displacement_atr"] * atr_i)
        lo_h, hi_h = SPEC["zone_height_bounds_atr"]
        height = zone_top - zone_bottom
        height_ok = atr_i > 0 and lo_h * atr_i <= height <= hi_h * atr_i
        trend_ok = (trend_h1 == 1 and trend_h4 == 1) if side == "LONG" \
            else (trend_h1 == -1 and trend_h4 == -1)
        if side == "LONG":
            sl = zone_bottom - SPEC["sl_buffer_atr"] * atr_i
            risk = zone_top - sl
        else:
            sl = zone_top + SPEC["sl_buffer_atr"] * atr_i
            risk = sl - zone_top
        cost = self.cost if self.cost is not None else 0.0
        risk_ok = (atr_i > 0 and risk > 0
                   and risk >= SPEC["min_risk_cost_multiple"] * cost
                   and risk <= SPEC["max_risk_atr"] * atr_i)
        qualified = sweep and trend_ok and risk_ok and disp_ok and height_ok
        return {"status": SIGNAL if qualified else
                ("ZONE_FORMED_UNQUALIFIED" if (disp_ok and height_ok) else NO_SIGNAL),
                "strategy": SPEC["name"], "direction": side,
                "created_at_pos": i, "zone_top": zone_top,
                "zone_bottom": zone_bottom, "stop_loss": sl,
                "risk_distance": risk, "atr": atr_i,
                "displacement_ok": disp_ok, "height_ok": height_ok,
                "sweep": sweep, "trend_ok": trend_ok, "risk_ok": risk_ok,
                "zone_live_bars": SPEC["zone_life_bars"],
                "tp_multiple": SPEC["tp_multiple"], "cost": cost}


# ---------------------------------------------------------------------------
# evaluate(): zone creation on the LATEST closed M15 bar (research preview)
# ---------------------------------------------------------------------------
def evaluate(m15, h1, h4, market: Optional[str] = None,
             cost: Optional[float] = None,
             current_time_utc=None) -> Dict[str, Any]:
    bad = check_market(market, cost)
    if bad:
        return {"status": bad, "strategy": SPEC["name"],
                "reason": (f"{market} is not in the spec's market list or has "
                           "no round-trip cost configured")}
    try:
        df = _frame(m15, "M15")
        h1f = _frame(h1, "H1")
        h4f = _frame(h4, "H4")
    except ValueError as exc:
        return {"status": INSUFFICIENT, "strategy": SPEC["name"], "reason": str(exc)}
    _m15_min = SPEC["swing_len"] * 3 + 3      # two swing windows + BOS bar
    if len(df) < _m15_min or len(h1f) < 200 or len(h4f) < 200:
        return {"status": INSUFFICIENT, "strategy": SPEC["name"],
                "reason": (f"need >= {_m15_min} M15 / 200 H1 / 200 H4 bars, got "
                           f"{len(df)}/{len(h1f)}/{len(h4f)}")}
    atr = _atr_simple(df, SPEC["atr_period"])
    t_h1, t_h4 = _trend_state(h1f), _trend_state(h4f)
    eng = ZoneEngine(cost)
    i = len(df) - 1
    for k in range(0, i):                    # replay history to build swing state
        eng.on_bar(df, k, float(atr.iloc[k]) if pd.notna(atr.iloc[k]) else 0.0,
                   _trend_asof(t_h1, df.index[k]), _trend_asof(t_h4, df.index[k]))
    zone = eng.on_bar(df, i, float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0,
                      _trend_asof(t_h1, df.index[i]), _trend_asof(t_h4, df.index[i]))
    if zone is None:
        return {"status": NO_SIGNAL, "strategy": SPEC["name"],
                "timestamp": str(df.index[i])}
    zone["market"] = market
    zone["timestamp"] = str(df.index[i])
    if zone["status"] == SIGNAL:
        tp_dist = SPEC["tp_multiple"] * zone["risk_distance"]
        sign = 1.0 if zone["direction"] == "LONG" else -1.0
        zone["take_profit"] = zone["zone_top"] + sign * tp_dist   # limit-based entry
        zone["entry_limit"] = zone["zone_top"]
    return zone


# ---------------------------------------------------------------------------
# live entry helper - the spec's limit-entry moment on the LATEST closed bar
# (research rules only; the caller decides what to do with it)
# ---------------------------------------------------------------------------
def zone_entry_event(m15, h1, h4, market: Optional[str] = None,
                     cost: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Replay the closed M15 history through the zone lifecycle and return a
    SIGNAL dict ONLY when the LATEST closed bar is the FIRST touch of a
    qualified zone's limit - the exact moment the spec's limit order would
    fill. None otherwise (no zone, stale touch, cancelled zone, expired
    zone). Stateless: every scan rebuilds from candles (restart-safe)."""
    bad = check_market(market, cost)
    if bad:
        return None
    try:
        df = _frame(m15, "M15")
        h1f = _frame(h1, "H1")
        h4f = _frame(h4, "H4")
    except ValueError:
        return None
    _m15_min = SPEC["swing_len"] * 3 + 3
    if len(df) < _m15_min or len(h1f) < 200 or len(h4f) < 200:
        return None
    atr = _atr_simple(df, SPEC["atr_period"])
    t_h1, t_h4 = _trend_state(h1f), _trend_state(h4f)
    eng = ZoneEngine(cost if cost is not None else round_trip_cost(market))
    n = len(df)
    pending: List[Dict[str, Any]] = []       # qualified zones waiting for touch
    latest_touch: Optional[Dict[str, Any]] = None
    for i in range(10, n):
        atr_i = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0
        zone = eng.on_bar(df, i, atr_i,
                          _trend_asof(t_h1, df.index[i]),
                          _trend_asof(t_h4, df.index[i]))
        if zone and zone["status"] == SIGNAL:
            pending.append(zone)
        still = []
        for z in pending:
            sign = 1 if z["direction"] == "LONG" else -1
            if i <= z["created_at_pos"]:
                still.append(z)
                continue
            o = float(df["open"].iloc[i])
            hi_ = float(df["high"].iloc[i])
            lo_ = float(df["low"].iloc[i])
            cl = float(df["close"].iloc[i])
            touched = (lo_ <= z["zone_top"]) if sign == 1 else (hi_ >= z["zone_bottom"])
            if touched:                       # FIRST touch = the fill moment
                if i == n - 1:
                    limit_px = z["zone_top"] if sign == 1 else z["zone_bottom"]
                    entry = o if ((sign == 1 and o < z["zone_top"]) or
                                  (sign == -1 and o > z["zone_bottom"])) else limit_px
                    risk = (entry - z["stop_loss"]) * sign
                    if risk <= 0:
                        continue              # degenerate fill - never a signal
                    latest_touch = {
                        "status": SIGNAL, "strategy": SPEC["name"],
                        "direction": z["direction"], "market": market,
                        "entry_limit": limit_px, "entry": entry,
                        "stop_loss": z["stop_loss"],
                        "risk_distance": abs(risk),
                        "take_profit": entry + sign * SPEC["tp_multiple"] * abs(risk),
                        "touch_bar": str(df.index[i]),
                        "zone_created_at": str(df.index[z["created_at_pos"]]),
                        "zone_top": z["zone_top"], "zone_bottom": z["zone_bottom"],
                        "sweep": z["sweep"], "atr": z["atr"],
                        "tp_multiple": SPEC["tp_multiple"],
                        "risk_reward": f"1:{SPEC['tp_multiple']:g}",
                        "timestamp": str(df.index[i])}
                # zone consumed either way (one trade per zone)
                continue
            if (sign == 1 and cl < z["zone_bottom"]) or \
                    (sign == -1 and cl > z["zone_top"]):
                continue                      # closed through, never touched
            if i >= z["created_at_pos"] + SPEC["zone_life_bars"]:
                continue                      # zone expired untouched
            still.append(z)
        pending = still
    return latest_touch


# ---------------------------------------------------------------------------
# backtest simulator - entry/exit rules ONLY, 1 unit, R multiples
# ---------------------------------------------------------------------------
def backtest(m15, h1, h4, market: str, cost: Optional[float] = None) -> Dict[str, Any]:
    bad = check_market(market, cost)
    if bad:
        return {"status": bad, "strategy": SPEC["name"], "trades": [],
                "summary": {"n": 0}}
    df = _frame(m15, "M15")
    h1f, h4f = _frame(h1, "H1"), _frame(h4, "H4")
    atr = _atr_simple(df, SPEC["atr_period"])
    t_h1, t_h4 = _trend_state(h1f), _trend_state(h4f)
    eng = ZoneEngine(cost if cost is not None else round_trip_cost(market))
    trades: List[Dict[str, Any]] = []
    n = len(df)
    for i in range(10, n):
        atr_i = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0
        zone = eng.on_bar(df, i, atr_i,
                          _trend_asof(t_h1, df.index[i]),
                          _trend_asof(t_h4, df.index[i]))
        if not zone or zone["status"] != SIGNAL:
            continue
        sign = 1.0 if zone["direction"] == "LONG" else -1.0
        limit_px = zone["zone_top"] if sign == 1 else zone["zone_bottom"]
        sl = zone["stop_loss"]
        # zone lifecycle: bars i+1 .. i+96 (cancel on close-through, or fill)
        fill_j = None
        for j in range(i + 1, min(n, i + 1 + SPEC["zone_life_bars"])):
            o, hi_, lo_, cl = (float(df["open"].iloc[j]), float(df["high"].iloc[j]),
                               float(df["low"].iloc[j]), float(df["close"].iloc[j]))
            # TOUCH first (a bar that both touches the zone and closes through
            # it has REACHED the zone - the cancel rule only applies to bars
            # BEFORE the touch, per the spec's "before it is touched").
            touched = (lo_ <= zone["zone_top"]) if sign == 1 else (hi_ >= zone["zone_bottom"])
            if touched:
                fill_j = j
                entry = o if ((sign == 1 and o < zone["zone_top"]) or
                              (sign == -1 and o > zone["zone_bottom"])) else limit_px
                break
            if (sign == 1 and cl < zone["zone_bottom"]) or \
                    (sign == -1 and cl > zone["zone_top"]):
                break                        # closed through, never touched
        if fill_j is None:
            continue
        risk = (entry - sl) * sign
        if risk <= 0:
            continue                          # degenerate fill - never traded
        tp = entry + sign * SPEC["tp_multiple"] * risk
        end = min(n - 1, fill_j + SPEC["time_exit_bars_after_fill"])
        exit_px = exit_reason = exit_t = None
        j = fill_j
        while j <= end:
            o, hi_, lo_, cl = (float(df["open"].iloc[j]), float(df["high"].iloc[j]),
                               float(df["low"].iloc[j]), float(df["close"].iloc[j]))
            hit_stop = (o <= sl) if sign == 1 else (o >= sl)
            if hit_stop:                      # gap through the stop fills at the open
                exit_px, exit_reason = o, "STOP_GAP_OPEN"
            elif (lo_ <= sl) if sign == 1 else (hi_ >= sl):
                exit_px, exit_reason = sl, "STOP"    # fill-bar stop counts (spec)
            elif j > fill_j and ((hi_ >= tp) if sign == 1 else (lo_ <= tp)):
                exit_px, exit_reason = tp, "TAKE_PROFIT"     # targets from the NEXT bar
            elif j == end:
                exit_px, exit_reason = cl, "TIME_EXIT_96_AFTER_FILL"
            if exit_px is not None:
                exit_t = df.index[j]
                break
            j += 1
        r = ((exit_px - entry) / risk) if sign == 1 else ((entry - exit_px) / risk)
        trades.append({
            "market": market, "direction": zone["direction"],
            "created_at": str(df.index[i]), "fill_time": str(df.index[fill_j]),
            "entry": round(entry, 6), "zone_top": round(zone["zone_top"], 6),
            "zone_bottom": round(zone["zone_bottom"], 6),
            "stop_loss": round(sl, 6), "take_profit": round(tp, 6),
            "exit_time": str(exit_t), "exit": round(exit_px, 6),
            "exit_reason": exit_reason, "r_multiple": round(r, 4),
            "bars_after_fill": j - fill_j + 1})
    return {"status": "OK", "strategy": SPEC["name"], "trades": trades,
            "summary": _summarize(trades), "reference": SPEC["reference"]}


def _summarize(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"n": 0}
    rs = [t["r_multiple"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    worst = 0
    cur = 0
    for r in rs:
        cur = cur + 1 if r <= 0 else 0
        worst = max(worst, cur)
    return {"n": len(rs),
            "win_rate": round(len(wins) / len(rs), 4),
            "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
            "avg_loss_r": round(sum(losses) / len(losses), 4) if losses else 0.0,
            "profit_factor": round(gross_w / gross_l, 4) if gross_l else None,
            "expectancy_r": round(sum(rs) / len(rs), 4),
            "total_r": round(sum(rs), 4),
            "longest_losing_streak": worst}
