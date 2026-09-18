"""AI trade management (master prompt SS10-SS20, SS32-SS37, SS46).

STRICT ORDER (SS1-SS9): the strategy owns entries. This package starts
AFTER a position exists:

    STRATEGY -> SIGNAL -> ENTRY -> [ this package ] -> RISK GATE -> MT5

The AI never vetoes, delays or creates entries; it only assesses whether an
OPEN position still has evidence for continuation and proposes management
actions (HOLD / PROTECT / PARTIAL_PROFIT / EXIT) that a deterministic risk
gate validates before MT5 sees anything.
"""
