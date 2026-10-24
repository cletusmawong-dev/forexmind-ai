"""TPC-15 Trend Pullback Confirmation - the ONLY registered strategies stay
the two owner gold specs; this package is deliberately NOT imported there.
"""
from .strategy import (CONFIG, evaluate, evaluate_setup, finalize_entry,
                       compute_emas, inside_session, setup_id_for)

__all__ = ["CONFIG", "evaluate", "evaluate_setup", "finalize_entry",
           "compute_emas", "inside_session", "setup_id_for"]
