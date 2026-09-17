"""Pydantic schemas for API requests/responses (core records)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import field_validator, model_validator, BaseModel, Field


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
    allowed_markets: List[str] = Field(
        default_factory=lambda: ["XAUUSD", "NAS100", "EURUSD", "GBPUSD", "USDJPY"])
    account_type: str = "personal"  # "personal" | "propfirm"
    prop_rules: Optional[PropRules] = None

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
