"""ForexMind AI Brain 2.0 (Master Upgrade Stage 2).

Layered reasoning over a structured world model:

    world_model.build_world_model()   pure data, no AI, freshness-audited
            |
    brain.run_brain()                 OBSERVE -> GENERATE -> CHALLENGE ->
            |                         DECIDE -> (deterministic gate = caller)
    risk_gate.validate_action()       UNCHANGED deterministic gate - the ONLY
            |                         path to MT5 primitives
    recorder.record()                 full audit incl. brain metadata

The brain may honestly answer NO_ACTION / INSUFFICIENT_EVIDENCE / DATA_STALE /
CONFLICTING instead of forcing a management action. Confidence is an
EVIDENCE score (deterministic blend, 0-100%) and NEVER moves entry/SL/TP.
"""
