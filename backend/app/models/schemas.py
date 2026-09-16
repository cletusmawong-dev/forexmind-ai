"""Pydantic schemas for API requests/responses (core records)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import field_validator, BaseModel, Field


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
