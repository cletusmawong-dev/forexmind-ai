# FOREXMIND 3.0 — STAGE 1 IMPLEMENTATION REPORT (per spec §68)
**Date:** 2026-09-28 · **Scope:** Stage 1 (Foundation) complete + Stage 2 core (brain EVIDENCE stage) + Stage 3 item 14 (degradation engine) · ** NOTHING DEPLOYED.**

---

## 1) Architecture changes
- New domain module `app/evidence/` (engine + brain bridge) implementing the Evidence Engine as an independent, deterministic layer. It consumes existing structures only (completed signals, learning matrix, experiment docs) — no new AI calls, no duplication of matrix/hypotheses/versions systems.
- AI Brain pipeline upgraded **OBSERVE → GENERATE → CHALLENGE → EVIDENCE → DECIDE** via an additive wrapper: `_run_brain` (unchanged inner pipeline) + `run_brain` (annotates every exit path with the deterministic evidence view + epistemic type). The decision itself is never modified by evidence — evidence is context, the deterministic gate remains the sole authority.
- Strategy degradation engine added as **reporting-only** (`app/learning/health.py`) per §14's "do not auto-pause on normal statistical variation".
- Autonomous research cycle (`auto_loop.run_research_cycle`) now refreshes evidence every cycle (`evidence_refreshed` count).

## 2) Backend files changed
- **NEW** `app/evidence/__init__.py`, `app/evidence/engine.py` (data model, scoring, contradiction, age/decay, upsert, refresh_all), `app/evidence/brain_bridge.py` (attach_evidence)
- **NEW** `app/api/routes_evidence.py` · **NEW** `app/learning/health.py`
- `app/ai/brain.py` (run_brain → _run_brain + wrapper; CHALLENGE_INSTRUCTIONS extended with the 8 structured spec questions)
- `app/api/routes_learning.py` (+ strategy-health route) · `app/main.py` (router registration) · `app/db/store.py` ("evidence" added to COLLECTIONS) · `app/learning/auto_loop.py` (evidence refresh step)

## 3) Frontend files changed
**None.** (Evidence UI is Stage 9 item 40 — deferred in dependency order.) Typecheck/build still verified green (items 13–14).

## 4) Database/schema changes
- New collection **`evidence`** registered in `COLLECTIONS` (LocalStore init + Supabase store share the constant; Firestore needs no schema change).
- Evidence doc carries every §3 field: evidence_id, user_id, subject_type, subject_id, strategy_id, instrument, timeframe, session, regime, conclusion, state, score, score_components, sample_size, historical_similarity, regime_consistency, oos_strength, walk_forward_strength, recent_performance, execution_quality, data_quality, contradiction_strength, evidence_age_days, first/last_observed_at, last_refresh_at, model_confidence (always null), calibrated_probability (null + `CALIBRATION_INSUFFICIENT`), historically_supported, currently_supported, supporting_factors, contradictions, missing_evidence, limitations, recent_view, created_at, updated_at, refresh_count.
- **Migration required when Supabase flip happens:** one `evidence` table (id + jsonb data, like the other 29) — noted for the gated runbook; NOT applied now.

## 5) API endpoints added/modified
- `GET /api/evidence` (list + per-state counts; filters: subject_type, strategy_id)
- `GET /api/evidence/{evidence_id}` (404 cross-user — isolation tested)
- `POST /api/evidence/generate` (recompute one subject or refresh all traded strategy×market pairs)
- `GET /api/learning/strategy-health/{strategy_id}` (new)
- All user-scoped via the existing `get_user_id` dependency; existing endpoints untouched.

## 6) AI Brain changes
- Pipeline is now 5-stage; **every** exit path (ACTION, NO_ACTION, INSUFFICIENT_EVIDENCE, DATA_STALE, CONFLICTING, router error, invalid JSON) carries `evidence` + `epistemic` annotation (DECISION vs OBSERVATION).
- Challenge stage must now explicitly answer the 8 spec questions (supports/contradicts/missing/sample/comparability/regime/execution/falsification).
- `model_confidence` in evidence objects is permanently null: **LLM confidence and evidence strength are structurally separate** (spec §3/§60: EVIDENCE WINS).
- Brain contract preserved: never raises; evidence failure degrades to `evidence: None` + `evidence_error` (tested by injecting a crash).

## 7) Evidence Engine changes (new)
- Deterministic, reproducible scoring: fixed weights (sample .25, similarity .10, regime .10, OOS .15, recency .10, recent performance .10, execution .10, data quality .05, consistency .05), contradiction multiplier `(1 − 0.6·strength)`. Same store state ⇒ byte-identical score (tested).
- States: INSUFFICIENT (<20) → PRELIMINARY (20–49) → SUPPORTED (50–99, score ≥0.60) → STRONGER (100+, score ≥0.70 **and** recent form within 10pp of baseline). CONTRADICTED (internal): contradiction strength ≥0.6 **and** recent WR ≥15pp under baseline. Quality gates mean state is never from sample size alone.
- Contradiction engine (§5): recent collapse, current losing streak, regime split (≥25pp gap, n≥5 per side), open R-drawdown — always surfaced in the doc, never hidden.
- Age & decay (§6): evidence_age_days, first/last observed, last refresh; `historically_supported` vs `currently_supported` separated so old support never masquerades as current.
- OOS/walk-forward components read REAL experiment docs (walk-forward consistency from the existing experiment engine); absent experiments ⇒ 0 + explicit `missing_evidence` entries. Missing ≠ good.

## 8) Research changes
- Research cycle refreshes evidence continuously; evidence states are available to the recommendation flow as classification context (session advisor logic itself unchanged — its thresholds were already conservative).
- Probability calibration (§16) is **honestly stubbed at zero knowledge**: field exists, value always null, label `CALIBRATION_INSUFFICIENT` — no fabricated probabilities. Real calibration tracking (Brier/reliability) is Stage 5.

## 9) Execution changes
**None.** Execution paths, primitives, gates, TP locks: untouched (verified — no execution files in the commit).

## 10) Security changes
- Evidence routes enforce user isolation (tested: user B reading user A's evidence → 404; lists are empty cross-user).
- **Known vulnerability REMAINS OPEN by explicit decision:** `FOREXMIND_JWT_SECRET` is unset in production (public repo + dev fallback secret). §51 says "fix known secret-management vulnerabilities before continuing" — rotating is a production-credential change that invalidates all sessions and requires the owner's explicit word (§66: human approves). **Blocked on the word "rotate," not forgotten.** Admin password `Cletus` likewise awaiting a stronger value from the owner.

## 11) Tests added (15 — `tests/test_evidence_engine.py`)
State mapping (sample gates + quality overrides) · scoring determinism · INSUFFICIENT below 20 · SUPPORTED band + missing-evidence flags · contradiction recent-collapse downgrade with historic/current split · age fields · upsert-by-deterministic-id (created_at preserved, no duplicates) · API roundtrip + user isolation · data-quality flags · brain paths carry evidence + epistemic labels · brain survives evidence failure · health states (INSUFFICIENT/HEALTHY/DEGRADING/INVESTIGATION) + never-mutates · health route · refresh_all coverage · research-cycle wiring.

## 12) Existing test count/result
**429 passed, 0 failed, 0 regressions** (414 before this round).

## 13) Frontend typecheck
`npm run build` runs `tsc -b --noCheck` then vite: **PASS**. Full strict tsc (informational): same 3 pre-existing warnings in untouched files (AgentScreen comparison, PositionsScreen callback signature, SettingsScreen truthiness) — zero new issues.

## 14) Frontend build
**PASS** — vite build clean in 6.5s.

## 15) Migrations required
- Firestore: none (schemaless; collection created on first write).
- Supabase (gated flip): add `evidence` table to schema SQL + backfill list — queued into the existing runbook, not executed.

## 16) Unresolved risks
1. **JWT secret (top, open)** — see §10. One env PUT fixes it; awaiting explicit approval.
2. Admin password `Cletus` — awaiting owner-provided stronger value.
3. Evidence OOS component currently reflects experiment *consistency*, not true holdout sets — real OOS splitting arrives with Research Lab 2.0 (Stage 5); the missing_evidence list is honest about this today.
4. Single-experiment-doc source for OOS: if multiple experiments exist, the latest is used (documented in code).

## 17) Features intentionally deferred (dependency order, per spec §61)
- Stage 2 remainder: #8 AI debate/critic roles (needs event-driven escalation budget design), #9 hypothesis-engine enrichment with evidence fields (mechanism exists via hypotheses + lessons; enrichment queued).
- Stage 3 remainder: market fingerprint as a first-class queryable object (signal DNA already captures the snapshot per signal — will formalize), strategy performance DNA derived from matrix (signal DNA exists), strategy memory states lifecycle.
- Stages 4–9 in full: TCA, broker/infra intelligence, exposure engine, Monte Carlo, stress lab, shadow trading, canary/versioning extensions, decision replay, time machine, counterfactuals, chaos testing, kill-switch levels, incident center, knowledge graph, NL research, Command Center/Evidence UI.
- Nothing above was faked: no placeholder logic was committed.

## 18) Git commit hash
**`ac1299f`** (local only — parent `aff5e37`). Working tree clean.

## 19) Existing strategies not unintentionally changed — CONFIRMED
`git diff HEAD~1 -- backend/app/strategies backend/app/engine` is **empty**; S1/S2 signal logic, entry paths, session gating, TP logic, and the deterministic risk gate are byte-identical. Brain changes are annotation-only; degradation engine is reporting-only; evidence generation writes only to the new `evidence` collection.

## 20) NOTHING was deployed — CONFIRMED
- Commit `ac1299f` is **local**; `git push` was NOT executed (Render auto-deploys on push — push deliberately withheld).
- Render still serves `aff5e37` (pre-spec UI round). Netlify bundle unchanged this round.
- No environment changes, no credential changes, no production data touched.
