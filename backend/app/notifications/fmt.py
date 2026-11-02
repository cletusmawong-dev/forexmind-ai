"""Rich message formatting for the Telegram lifecycle messages.

Owner directive 2026-11-02 ("the messages sent to Telegram should be nice
and detailed"): every trade message carries the full numbers - entry, stop
with its dollar risk, every TP with its R multiple and dollar value, the
ticket, the signal id. Pure formatting only; broker values are fetched
lazily and EVERYTHING degrades gracefully to R-only when they are not
available (tests, offline bridges) - a missing number never breaks a send.
"""
from __future__ import annotations

from typing import Optional


def usd(x: Optional[float]) -> str:
    try:
        x = float(x)
        return (f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}")
    except Exception:
        return "n/a"


def px(x) -> str:
    try:
        return f"{float(x):.8g}"      # trader format: 3961.62, 1.11902, 157.603
    except Exception:
        return "n/a"


def _pip_size(market: str) -> float:
    try:
        from ..execution.mt5 import PIP_SIZE
        return float(PIP_SIZE.get(market) or 0)
    except Exception:
        return 0.0


def _pip_value(market: str) -> float:
    try:
        from ..execution.mt5 import _pip_value_usd
        return float(_pip_value_usd(market) or 0)
    except Exception:
        return 0.0


def move_usd(market: str, from_px, to_px, lots) -> Optional[float]:
    """True dollar value of a price move at `lots`, from live broker truth."""
    ps, pv = _pip_size(market), _pip_value(market)
    if not ps or not pv or lots in (None, "", 0):
        return None
    try:
        return abs(float(to_px) - float(from_px)) / ps * pv * float(lots)
    except Exception:
        return None


def ctx_line(sig: dict, lots=None) -> str:
    """`Strategy | Entry .. | SL .. (n pips) | lots | SIG-...` context line."""
    parts = [sig.get("strategy_name") or sig.get("strategy_id") or "-"]
    e, sl = sig.get("entry"), sig.get("sl")
    if e not in (None, "", 0):
        line = f"Entry {px(e)}"
        if sl not in (None, "", 0):
            ps = _pip_size(sig.get("market") or "")
            try:
                dist = abs(float(e) - float(sl))
                line = f"Entry {px(e)} | SL {px(sl)}"
                if ps:
                    line += f" ({dist / ps:,.1f} pips)"
                r = move_usd(sig.get("market"), e, sl, lots)
                if r is not None:
                    line += f" = risk {usd(r)}"
            except Exception:
                pass
        parts.append(line)
    if lots not in (None, "", 0):
        parts.append(f"{lots} lots")
    sid = sig.get("signal_id") or sig.get("id")
    if sid:
        parts.append(str(sid))
    return " | ".join(parts)


def ladder_lines(sig: dict, lots=None, ref_px=None) -> str:
    """`TP1 4,032.18 (+1.0R = +$70.56)` lines for every declared target."""
    try:
        e = float(ref_px if ref_px not in (None, "", 0) else sig["entry"])
    except Exception:
        return ""
    sl = sig.get("sl")
    try:
        risk = abs(e - float(sl)) if sl not in (None, "", 0) else 0.0
    except Exception:
        risk = 0.0
    order_tp = str(sig.get("order_tp") or "")
    out = []
    for k in (1, 2, 3):
        tp = sig.get(f"tp{k}")
        if tp in (None, "", 0):
            continue
        try:
            tp = float(tp)
        except Exception:
            continue
        r = (tp - e) / risk if risk else 0.0
        d = move_usd(sig.get("market"), e, tp, lots)
        line = f"TP{k} {px(tp)}  (+{r:.1f}R"
        line += f" = +{usd(d)})" if d is not None else ")"
        if order_tp in (str(tp), f"{tp:.5g}", f"{tp:g}"):
            line += "  <- order TP"
        out.append(line)
    return "\n".join(out)


def duration_str(created_at, closed_at) -> str:
    """`3h 12m` between two ISO stamps; '' when either is missing."""
    try:
        from datetime import datetime
        fmt = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")

        def parse(s):
            s = str(s).replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(s)
            except Exception:
                for f in fmt:
                    try:
                        return datetime.strptime(s[:len(f)], f)
                    except Exception:
                        continue
            return None

        a, b = parse(created_at), parse(closed_at)
        if not a or not b:
            return ""
        secs = int(abs((b.replace(tzinfo=None) if b.tzinfo is None else b)
                       - (a.replace(tzinfo=None) if a.tzinfo is None else a)
                       ).total_seconds())
        h, m = secs // 3600, (secs % 3600) // 60
        return f"{h}h {m:02d}m" if h else f"{m}m"
    except Exception:
        return ""
