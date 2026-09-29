import { useState } from "react";
import {ChevronDown, GitBranch, Layers, Clock} from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { StrategyDoc, StrategyVersion } from "../lib/types";
import { ActionButton, DemoTag, Divider, Eyebrow, Glass, GlowDot, Pill, Spinner } from "../components/ui";
import { useAsyncAction } from "../lib/useAsyncAction";

type EngineMode = "s1" | "s2" | "both" | "none";

const S1 = "strategy_1_zero_lag";
const S2 = "strategy_2_ema_atr";

const MODE_LABEL: Record<EngineMode, string> = {
  s1: "Zero Lag Trend only",
  s2: "9/21 EMA Smart TP/SL only",
  both: "both strategies",
  none: "no strategy - signals are OFF",
};

export function StrategiesScreen() {
  const { data, loading, refresh } = usePolling<{ strategies: StrategyDoc[] }>(() => api.get(endpoints.strategies), 8000);
  const [versions, setVersions] = useState<Record<string, StrategyVersion[]>>({});
  const [openId, setOpenId] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState("");

  const flash = (m: string) => {
    setToast(m);
    setTimeout(() => setToast(""), 4000);
  };

  const loadVersions = async (sid: string) => {
    if (openId === sid) {
      setOpenId(null);
      return;
    }
    setOpenId(sid);
    if (!versions[sid]) {
      const v = await api.get<{ versions: StrategyVersion[] }>(endpoints.strategyVersions(sid));
      setVersions((m) => ({ ...m, [sid]: v.versions }));
    }
  };

  const setStatus = async (sid: string, status: string) => {
    setBusy(sid + status);
    await api.patch(endpoints.strategy(sid), { status });
    setBusy("");
    refresh();
  };

  const engineMode: EngineMode = (() => {
    const list = data?.strategies ?? [];
    const s1on = list.find((s) => s.id === S1)?.status === "ACTIVE";
    const s2on = list.find((s) => s.id === S2)?.status === "ACTIVE";
    return s1on && s2on ? "both" : s1on ? "s1" : s2on ? "s2" : "none";
  })();

  const applyEngineMode = async (mode: EngineMode) => {
    if (mode === "none" || mode === engineMode) return;
    setBusy("engine");
    try {
      const plan: Record<string, [string, string]> = {
        s1: [S1, S2], s2: [S2, S1], both: [S1, S2],
      };
      const [on, off] = plan[mode];
      await api.patch(endpoints.strategy(on), { status: "ACTIVE" });
      if (mode !== "both") await api.patch(endpoints.strategy(off), { status: "PAUSED" });
      else await api.patch(endpoints.strategy(off), { status: "ACTIVE" });
      flash(`Signal engine: ${MODE_LABEL[mode]}. New signals only - tracked signals finish normally.`);
    } catch (e: any) {
      flash(e.message);
    } finally {
      setBusy("");
      refresh();
    }
  };

  const rollback = async (sid: string) => {
    if (!confirm("Roll back to the previous version? Version history is never deleted.")) return;
    setBusy(sid + "rb");
    try {
      const r = await api.post<{ rolled_back_to: StrategyVersion }>(endpoints.rollback(sid));
      flash(`Rolled back to v${r.rolled_back_to.version}`);
      const v = await api.get<{ versions: StrategyVersion[] }>(endpoints.strategyVersions(sid));
      setVersions((m) => ({ ...m, [sid]: v.versions }));
      refresh();
    } catch (e: any) {
      flash(e.message);
    }
    setBusy("");
  };

  if (loading && !data) return <Spinner label="Loading strategies..." />;

  return (
    <div className="animate-fadeUp">
      <header className="mb-7 flex items-start justify-between">
        <div className="flex items-center gap-3.5">
          <span className="icon-chip" aria-hidden="true"><Layers size={20} /></span>
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight">Strategies</h1>
          <p className="mt-1 text-[12.5px] text-txt-low">Registered research modules.</p>
          </div>
        </div>
        <DemoTag />
      </header>

      {toast && <div className="glass-2 mb-5 px-4 py-3 text-[12px] font-medium text-acc-cyan">{toast}</div>}

      {/* ---- signal engine selector: which strategy generates signals ---- */}
      <Glass className="mb-6">
        <div className="flex items-center justify-between">
          <Eyebrow>Signal engine</Eyebrow>
          <GlowDot tone={engineMode === "none" ? "warn" : "pos"} size={6} pulse={engineMode !== "none"} />
        </div>
        <p className="mt-1 text-[11.5px] leading-relaxed text-txt-low">
          Choose the strategy that generates signals. Currently:{" "}
          <span className={engineMode === "none" ? "font-semibold text-warn" : "font-semibold text-txt-hi"}>
            {MODE_LABEL[engineMode]}
          </span>
        </p>
        <div className={`mt-4 grid grid-cols-3 gap-2 ${busy === "engine" ? "pointer-events-none opacity-50" : ""}`}>
          {([["s1", "Strategy 1"], ["s2", "Strategy 2"], ["both", "Both"]] as const).map(([k, label]) => (
            <button
              key={k}
              onClick={() => applyEngineMode(k)}
              disabled={busy === "engine" || engineMode === k}
              aria-pressed={engineMode === k}
              className={`tap min-h-[46px] rounded-2xl border px-2 py-3 text-[12px] font-bold tracking-wide ${
                engineMode === k ? "chip-on" : "chip-off"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="mt-3 text-[10px] leading-relaxed text-txt-faint">
          Applies to new signals only. Signals already tracking always run to completion.
        </p>
      </Glass>

      <Glass pad={false} className="divide-y divide-[rgba(var(--warm-rgb),0.06)] !p-0">
        {(data?.strategies ?? []).map((s, idx) => (
          <div key={s.id} data-reveal style={{ transitionDelay: `${Math.min(idx * 60, 240)}ms` }}>
            <button onClick={() => loadVersions(s.id)} className="tap flex w-full items-center gap-4 px-5 py-5 text-left transition hover:bg-[rgba(var(--warm-rgb),0.03)]">
              {s.status === "ACTIVE" ? <GlowDot tone="pos" /> : s.status === "PAUSED" ? <GlowDot tone="warn" pulse={false} /> : <span className="h-[7px] w-[7px] rounded-full bg-[rgba(var(--warm-rgb),0.18)]" />}
              <div className="min-w-0 flex-1">
                <div className="text-[14.5px] font-medium tracking-tight text-txt-hi">{s.short_name}</div>
                <div className="mt-0.5 text-[11px] text-txt-faint">
                  Strategy {idx + 1} - <span className="font-mono">v{s.active_version}</span> - {s.status.toLowerCase()}
                </div>
              </div>
              <ChevronDown size={16} className={`shrink-0 text-txt-faint transition-transform duration-500 ${openId === s.id ? "rotate-180" : ""}`} />
            </button>

            <div className={`grid transition-all duration-500 ease-out ${openId === s.id ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
              <div className="overflow-hidden">
                <div className="px-5 pb-6">
                  <p className="text-[12px] leading-relaxed text-txt-low">{s.description}</p>

                  {/* parameters - quiet two-column */}
                  <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 rounded-2xl border border-[rgba(var(--warm-rgb),0.06)] bg-[rgba(var(--warm-rgb),0.035)] p-4">
                    {Object.entries(s.active_params ?? {})
                      .filter(([k]) => k !== "expire_bars")
                      .map(([k, v]) => (
                        <div key={k} className="flex items-baseline justify-between">
                          <span className="font-mono text-[10.5px] text-txt-faint">{k}</span>
                          <span className="num text-[12px] font-medium text-txt-mid">{String(v)}</span>
                        </div>
                      ))}
                  </div>

                  <StrategySessionsCard sid={s.id} />

                  {/* versions */}
                  {(versions[s.id] ?? []).length > 0 && (
                    <div className="mt-4">
                      <Eyebrow className="mb-2">Version history</Eyebrow>
                      <div className="space-y-1.5">
                        {(versions[s.id] ?? []).map((v) => (
                          <div key={v.id} className="flex items-center justify-between rounded-xl border border-[rgba(var(--warm-rgb),0.06)] bg-[rgba(var(--warm-rgb),0.035)] px-4 py-3">
                            <div className="flex items-center gap-3">
                              {v.active ? <GlowDot tone="pos" size={6} pulse={false} /> : <span className="h-[6px] w-[6px] rounded-full bg-[rgba(var(--warm-rgb),0.18)]" />}
                              <span className="num text-[12px] font-medium">v{v.version}</span>
                              <span className="text-[10.5px] text-txt-faint">{v.note}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* controls */}
                  <div className="mt-5 flex flex-wrap items-center gap-2">
                    {(["ACTIVE", "PAUSED", "DISABLED"] as const).map((st) => (
                      <button
                        key={st}
                        disabled={s.status === st || busy === s.id + st}
                        onClick={() => setStatus(s.id, st)}
                        className={`rounded-full border px-3.5 py-1.5 text-[10px] font-bold tracking-[0.1em] transition-all ${
                          s.status === st
                            ? st === "ACTIVE"
                              ? "chip-on-pos"
                              : st === "PAUSED"
                                ? "chip-on-warn"
                                : "chip-on-muted"
                            : "chip-off"
                        }`}
                      >
                        {st}
                      </button>
                    ))}
                    <button onClick={() => rollback(s.id)} disabled={busy === s.id + "rb"} className="tap flex items-center gap-1.5 rounded-full border border-warn/25 bg-warn/[0.07] px-3.5 py-1.5 text-[10px] font-semibold tracking-[0.1em] text-warn transition hover:bg-warn/[0.12]">
                      <GitBranch size={11} /> Roll back
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <Divider className="last:hidden" />
          </div>
        ))}
      </Glass>

      {/* add strategy - part of the interface */}
      <button className="tap mt-4 flex w-full items-center gap-4 rounded-[24px] border border-dashed border-[rgba(var(--warm-rgb),0.12)] px-5 py-5 text-left text-txt-low transition hover:border-[rgba(var(--warm-rgb),0.20)] hover:text-txt-mid">
        <span className="flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-[rgba(var(--warm-rgb),0.045)] text-[15px] font-light">+</span>
        <span>
          <span className="text-[13px] font-medium">Add a strategy</span>
          <span className="mt-0.5 block text-[10.5px] text-txt-faint">Register a module under backend/app/strategies - it appears everywhere automatically. The AI can never invent strategies.</span>
        </span>
      </button>
    </div>
  );
}

const ALL_SESSIONS = ["Asian", "London", "NewYork", "Late"] as const;

/** Sessions editor + AI session advisor (P6/P7): SET the sessions a strategy
 *  may trade (audited PATCH, enforced in both scan loops), see per-session
 *  performance from previous signals, and apply/reject the AI's best-session
 *  suggestions (always a DRAFT until you explicitly activate it). */
export function StrategySessionsCard({ sid }: { sid: string }) {
  const strategies = usePolling<{ strategies: (StrategyDoc & {
    sessions?: string[] | null; proposed_sessions?: string[] | null })[] }>(
    () => api.get(endpoints.strategies), 15000);
  const recs = usePolling<{ recommendations: any[] }>(
    () => api.get(endpoints.learningRecommendations + "?status=REVIEW"), 30000);
  const matrix = usePolling<{ cells: any[] }>(() => api.get(endpoints.learningMatrix), 60000);
  const save = useAsyncAction();
  const apply = useAsyncAction();
  const [picked, setPicked] = useState<string[] | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  const me = (strategies.data?.strategies ?? []).find((x) => x.id === sid);
  const current = me?.sessions ?? null;                 // null = all sessions
  const draft = me?.proposed_sessions ?? null;
  const selected = picked ?? (current ?? [...ALL_SESSIONS]);
  const dirty = picked !== null &&
    JSON.stringify([...picked].sort()) !== JSON.stringify([...(current ?? [...ALL_SESSIONS])].sort());

  const toggle = (ses: string) => {
    const base = picked ?? (current ?? [...ALL_SESSIONS]);
    setPicked(base.includes(ses) ? base.filter((x) => x !== ses) : [...base, ses]);
  };

  const myRecs = (recs.data?.recommendations ?? [])
    .filter((r) => r.strategy_id === sid &&
      (r.type === "SESSION_SUGGESTION" || r.type === "FILTER_SESSION"));

  // per-session performance for this strategy (previous signals)
  const perSession: Record<string, { n: number; wr: number; r: number; wins: number }> = {};
  for (const c of matrix.data?.cells ?? []) {
    if (c.strategy_id !== sid || !c.session) continue;
    const a = perSession[c.session] ?? (perSession[c.session] = { n: 0, wr: 0, r: 0, wins: 0 });
    a.n += c.n ?? 0;
    a.wins += ((c.win_rate ?? 0) * (c.n ?? 0)) / 100;
    a.r += (c.avg_r ?? 0) * (c.n ?? 0);
  }
  const sessionRows = Object.entries(perSession).map(([ses, a]) => ({
    ses, n: a.n, wr: a.n ? Math.round((100 * a.wins) / a.n) : 0,
    avgR: a.n ? a.r / a.n : 0,
  })).sort((a, b) => b.avgR - a.avgR);

  return (
    <div className="mt-4 rounded-2xl border border-[rgba(var(--warm-rgb),0.06)] bg-[rgba(var(--warm-rgb),0.035)] p-4" data-reveal>
      <div className="flex items-center justify-between">
        <Eyebrow className="!mb-0">Trading sessions <Clock size={11} className="ml-1 inline" /></Eyebrow>
        {dirty && <Pill tone="warn">unsaved</Pill>}
      </div>
      <p className="mt-1 text-[10.5px] text-txt-faint">
        This strategy only produces signals inside the selected sessions.
      </p>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {ALL_SESSIONS.map((ses) => (
          <button key={ses} onClick={() => toggle(ses)}
            className={`rounded-full border px-3 py-1.5 text-[10.5px] font-semibold transition ${
              selected.includes(ses) ? "chip-on-pos" : "chip-off"
            }`}>
            {ses}
          </button>
        ))}
        <span className="self-center pl-1">
          <ActionButton label="Save sessions" busy={save.busy} tone="pos"
            onRun={async () => {
              await save.run(() => api.patch(endpoints.strategy(sid), { sessions: selected }),
                "Saved - applies to every future scan");
              setPicked(null);
              strategies.refresh();
              return "Sessions saved";
            }} />
        </span>
      </div>
      {draft && draft.length > 0 && (
        <p className="mt-2 rounded-xl border border-[rgba(var(--amber-rgb),0.3)] bg-[rgba(var(--amber-rgb),0.07)] px-3 py-2 text-[10.5px] text-[var(--accent-amber)]">
          AI draft pending activation: {draft.join(", ")} - review it below, then Apply to stage it here and Save.
        </p>
      )}

      {/* per-session performance from previous signals */}
      {sessionRows.length > 0 && (
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {sessionRows.map((r) => (
            <div key={r.ses} className="rounded-xl border border-[rgba(var(--warm-rgb),0.06)] px-3 py-2">
              <div className="text-[11px] font-semibold text-txt-hi">{r.ses}</div>
              <div className={`num text-[13px] font-bold ${r.wr >= 50 ? "text-pos" : "text-[var(--accent-red)]"}`}>
                {r.wr}%<span className="ml-1 text-[9px] font-normal text-txt-faint">n={r.n}</span>
              </div>
              <div className="num text-[9.5px] text-txt-faint">{r.avgR >= 0 ? "+" : ""}{r.avgR.toFixed(2)}R avg</div>
            </div>
          ))}
        </div>
      )}

      {/* AI advisor */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          disabled={analyzing}
          onClick={async () => {
            setAnalyzing(true);
            try {
              await api.post(endpoints.learningRecsGenerate, {});
              recs.refresh(); matrix.refresh();
            } catch { /* surfaced via list absence + toast upstream */ }
            setAnalyzing(false);
          }}
          className="tap rounded-lg border border-[rgba(var(--p-rgb),0.25)] px-2.5 py-1.5 text-[11px] font-medium text-txt-mid hover:bg-[rgba(var(--p-rgb),0.08)] disabled:opacity-50">
          {analyzing ? "Analyzing previous signals…" : "AI: analyze best sessions"}
        </button>
        {myRecs.length === 0 && <span className="text-[10.5px] text-txt-faint">No suggestions yet - needs ~20 signals.</span>}
      </div>
      <div className="mt-2 space-y-2">
        {myRecs.map((r) => (
          <div key={r.id} className="rounded-xl border border-[rgba(var(--p-rgb),0.18)] px-3 py-2.5">
            <div className="flex items-center justify-between gap-2">
              <Pill tone={r.type === "SESSION_SUGGESTION" ? "cyan" : "warn"}>{r.type === "SESSION_SUGGESTION" ? "best sessions" : "weak session"}</Pill>
              <span className="text-[9.5px] text-txt-faint">{r.market}</span>
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-txt-mid">{r.claim}</p>
            <div className="mt-2 flex items-center gap-2">
              <ActionButton label="Apply draft" tone="pos" busy={apply.busy}
                onRun={async () => {
                  const res = await apply.run(() => api.post(`/api/learning/recommendations/${r.id}/apply`, {}), "drafted");
                  setPicked(res?.recommendation?.applied_draft?.proposed_sessions ?? null);
                  recs.refresh(); strategies.refresh();
                  return "Draft staged - review the chips and Save";
                }} />
              <ActionButton label="Reject" tone="danger" busy={apply.busy}
                onRun={() => apply.run(() => api.post(`/api/learning/recommendations/${r.id}/reject`, { reason: "user rejected" }), "rejected")
                  .then(() => { recs.refresh(); return "Rejected"; })} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
