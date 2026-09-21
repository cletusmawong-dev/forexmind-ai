"""Pydantic schemas for API requests/responses (core records)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import field_validator, model_validator, BaseModel, Field

from ..config import INITIAL_MARKETS

SESSION_NAMES = ("Asian", "London", "NewYork", "Late")


# ---------------------------------------------------------------- auth
class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=6)
    display_name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------- settings / goals
class GoalSettings(BaseModel):
    account_balance: float = 20.0
    daily_objective_pct: float = 1.5
    weekly_objective_pct: float = 5.0


ALLOWED_SIGNAL_TIMEFRAMES = ["15M", "1H", "4H", "1D"]


class PropRules(BaseModel):
    """Prop-firm account rules (user-requested 2026-09-17). The signal engine
    enforces these as hard signal-generation limits - execution stays manual."""
    daily_drawdown_pct: float = Field(default=5.0)      # max loss/day (% of start balance)
    max_total_drawdown_pct: float = Field(default=10.0)  # max overall drawdown
    profit_target_pct: float = Field(default=8.0)        # phase profit target
    daily_dd_buffer_pct: float = Field(default=20.0)     # stop new signals at (100-buffer)% of the daily limit
    account_start_balance: float = Field(default=0.0)    # 0 = use current goals balance

    @field_validator("daily_drawdown_pct", "max_total_drawdown_pct", "profit_target_pct")
    @classmethod
    def _positive(cls, v, info):
        cap = {"daily_drawdown_pct": 50.0, "max_total_drawdown_pct": 90.0, "profit_target_pct": 200.0}[info.field_name]
        if not (0 < v <= cap):
            raise ValueError(f"{info.field_name} must be within (0, {cap}]")
        return float(v)

    @field_validator("daily_dd_buffer_pct")
    @classmethod
    def _buffer(cls, v):
        if not (0 <= v <= 50):
            raise ValueError("daily_dd_buffer_pct must be within [0, 50]")
        return float(v)

    @field_validator("account_start_balance")
    @classmethod
    def _balance(cls, v):
        if v < 0:
            raise ValueError("account_start_balance cannot be negative")
        return float(v)


class RiskSettings(BaseModel):
    signal_timeframes: List[str] = Field(default_factory=lambda: ["15M"])
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_consecutive_losses: int = 4
    max_signals_per_day: int = 6
    min_rr: float = 1.5
    sessions: List[str] = Field(default_factory=lambda: ["London", "NewYork", "Asian", "Late"])
    daily_profit_target_usd: float = Field(default=0.0)   # 0 = off (SS21)
    daily_loss_limit_usd: float = Field(default=0.0)      # 0 = off (SS25)
    on_loss_limit: str = "stop_entries"
    ai_manage_enabled: bool = False                    # AI trade manager opt-in
    tp1_policy: str = "ai_decide"                      # | "protect" | "partial"
    tp3_policy: str = "close"                          # final-profit behavior                   # | "stop_and_close"
    market_sessions: Dict[str, List[str]] = Field(default_factory=dict)
    session_hours: Dict[str, List[int]] = Field(default_factory=dict)
    session_tz: str = "UTC"
    allowed_markets: List[str] = Field(
        default_factory=lambda: ["XAUUSD", "NAS100", "EURUSD", "GBPUSD", "USDJPY"])
    account_type: str = "personal"  # "personal" | "propfirm"
    lot_mode: str = "medium"        # "low" | "medium" | "high" lot-size mode
    prop_rules: Optional[PropRules] = None

    @field_validator("sessions")
    @classmethod
    def _validate_sessions(cls, v):
        canon = {n.lower(): n for n in SESSION_NAMES}
        out = []
        for s in v or []:
            sc = canon.get(str(s).strip().lower())
            if not sc:
                raise ValueError(f"unknown session {s!r} - choose from {list(SESSION_NAMES)}")
            if sc not in out:
                out.append(sc)
        return out  # empty list allowed = deliberate pause (session gate blocks all)

    @field_validator("market_sessions")
    @classmethod
    def _validate_market_sessions(cls, v):
        """Per-market session matrix (SS31): {market: [sessions]}. An entry
        REPLACES the global session list for that market; an empty list pauses
        that market; a missing key follows the global list."""
        known = set(INITIAL_MARKETS) | {"NAS100"}
        canon = {n.lower(): n for n in SESSION_NAMES}
        out = {}
        for mk, sess in (v or {}).items():
            mu = str(mk).strip().upper()
            if mu not in known:
                raise ValueError(f"unknown market {mu!r} in market_sessions")
            lst = []
            for s in sess or []:
                sc = canon.get(str(s).strip().lower())
                if not sc:
                    raise ValueError(f"unknown session {s!r} for {mu} in market_sessions")
                if sc not in lst:
                    lst.append(sc)
            out[mu] = lst
        return out

    @field_validator("session_hours")
    @classmethod
    def _validate_session_hours(cls, v):
        """Custom session windows in the user's session_tz: {session: [start, end]}."""
        canon = {n.lower(): n for n in SESSION_NAMES}
        out = {}
        for name, win in (v or {}).items():
            nc = canon.get(str(name).strip().lower())
            if not nc:
                raise ValueError(f"unknown session {name!r} in session_hours")
            try:
                a, b = int(win[0]), int(win[1])
            except Exception:
                raise ValueError(f"session_hours[{nc}] must be [start_hour, end_hour]")
            if not (0 <= a < b <= 24):
                raise ValueError(f"session_hours[{nc}] must satisfy 0 <= start < end <= 24")
            out[nc] = [a, b]
        return out

    @field_validator("daily_profit_target_usd", "daily_loss_limit_usd")
    @classmethod
    def _validate_daily_usd(cls, v, info):
        f = abs(float(v or 0.0))
        if f > 100000:
            raise ValueError(f"{info.field_name} must be 0 (off) or <= 100000 USD")
        return round(f, 2)

    @field_validator("tp1_policy", "tp3_policy")
    @classmethod
    def _validate_tp_policies(cls, v, info):
        val = (str(v or "")).strip().lower()
        allowed = {"tp1_policy": ("ai_decide", "protect", "partial"),
                   "tp3_policy": ("close", "hold_ai")}[info.field_name]
        if val not in allowed:
            raise ValueError(f"{info.field_name} must be one of {allowed}")
        return val

    @field_validator("on_loss_limit")
    @classmethod
    def _validate_on_loss_limit(cls, v):
        m = (str(v or "stop_entries")).strip().lower()
        if m not in ("stop_entries", "stop_and_close"):
            raise ValueError("on_loss_limit must be 'stop_entries' or 'stop_and_close'")
        return m

    @field_validator("session_tz")
    @classmethod
    def _validate_session_tz(cls, v):
        tz = (str(v) if v is not None else "UTC").strip() or "UTC"
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(tz)
        except Exception:
            try:   # users type lowercase: africa/accra -> Africa/Accra
                tz = tz.title()
                ZoneInfo(tz)
            except Exception:
                raise ValueError(f"unknown timezone {tz!r} - IANA name expected (e.g. Africa/Accra)")
        return tz

    @field_validator("allowed_markets")
    @classmethod
    def _validate_markets(cls, v):
        # Normalize user input; broker-suffix names (XAUUSDm) belong to the
        # bridge layer, not here - a suffix name would never be scanned.
        # NAS100 is accepted: it is a real app market (server-side off unless
        # MARKETS_EXTRA is set).
        known = set(INITIAL_MARKETS) | {"NAS100"}
        out = []
        for m in v or []:
            mu = str(m).strip().upper()
            if mu not in known:
                raise ValueError(
                    f"unknown market {mu!r} - choose from {sorted(known)}. "
                    "Broker symbol suffixes (e.g. XAUUSDm) are resolved on the "
                    "VPS bridge, not configured here.")
            if mu not in out:
                out.append(mu)
        return out  # empty list allowed = deliberate pause of all markets

    @field_validator("signal_timeframes")
    @classmethod
    def _validate_timeframes(cls, v):
        if not v:
            raise ValueError("signal_timeframes cannot be empty - pick at least one of 15M, 1H, 4H, 1D")
        bad = [t for t in v if t not in ALLOWED_SIGNAL_TIMEFRAMES]
        if bad:
            raise ValueError(
                f"unsupported timeframe(s) {bad} - choose from {ALLOWED_SIGNAL_TIMEFRAMES}. "
                "5M is not offered: it would exceed the free market-data quota.")
        return list(dict.fromkeys(v))  # dedupe, keep order

    @field_validator("account_type")
    @classmethod
    def _validate_account_type(cls, v):
        v = (v or "personal").strip().lower()
        if v not in ("personal", "propfirm"):
            raise ValueError("account_type must be 'personal' or 'propfirm'")
        return v

    @model_validator(mode="after")
    def _prop_defaults(self):
        if self.account_type == "propfirm" and self.prop_rules is None:
            self.prop_rules = PropRules()  # sane defaults; user tunes in Settings
        return self


# ---------------------------------------------------------------- signals
class SignalActionIn(BaseModel):
    action: str  # "entered" | "skipped"


class ManualResultIn(BaseModel):
    """SS24: the user manually took an (EXTRA) signal. Kept SEPARATE from
    automatically executed trades (user_manual vs execution_status/mt5_*)."""
    taken: bool = True
    entry_price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    pl: Optional[float] = None
    result: Optional[str] = None      # WIN | LOSS | BREAK_EVEN | OPEN
    exit_reason: Optional[str] = None

    @field_validator("result")
    @classmethod
    def _validate_result(cls, v):
        if v is None:
            return None
        r = str(v).strip().upper()
        if r not in ("WIN", "LOSS", "BREAK_EVEN", "OPEN"):
            raise ValueError("result must be WIN | LOSS | BREAK_EVEN | OPEN")
        return r
    entry_price: Optional[float] = None
    lot_size: Optional[float] = None
    notes: str = ""
    screenshot_uri: str = ""


class AnalyzeIn(BaseModel):
    market: str
    timeframe: str = "15M"


# ---------------------------------------------------------------- trades
class TradeIn(BaseModel):
    signal_id: Optional[str] = None
    market: str
    direction: str
    entry_price: float
    sl: float
    tp: Optional[float] = None
    lot_size: float = 0.0
    notes: str = ""


class TradePatch(BaseModel):
    exit_price: Optional[float] = None
    notes: Optional[str] = None
    status: Optional[str] = None


# ---------------------------------------------------------------- learning
class HypothesisIn(BaseModel):
    strategy_id: str
    variable: str
    old_value: Optional[Any] = None   # resolved from the ACTIVE version server-side
    new_value: Any
    reason: str
    expected_effect: str
    dataset: Dict[str, Any] = Field(default_factory=dict)


class ExperimentIn(BaseModel):
    hypothesis_id: str


class ChatIn(BaseModel):
    message: str
