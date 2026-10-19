"""FOREXMIND gold spec strategies (owner specs 2026-10-10)."""
from .gold_orb import evaluate as evaluate_gold_orb
from .h1_trend_breakout import evaluate as evaluate_h1_trend_breakout
from .strategy import GoldOrbStrategy, H1TrendBreakoutStrategy

__all__ = ["evaluate_gold_orb", "evaluate_h1_trend_breakout",
           "GoldOrbStrategy", "H1TrendBreakoutStrategy"]
