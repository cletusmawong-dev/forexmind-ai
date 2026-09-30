import { useState } from "react";
import {ChevronDown, GitBranch, Layers, Clock} from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { StrategyDoc, StrategyVersion } from "../lib/types";
import { ActionButton, DemoTag, Divider, Eyebrow, Glass, GlowDot, Pill, Spinner } from "../components/ui";
import { useAsyncAction } from "../lib/useAsyncAction";


export function StrategiesScreen() {
  const { data, loading, refresh } = usePolling<{ strategies: StrategyDoc[] }>(() => api.get(endpoints.strategies), 8000);
  const [versions, setVersions] = useState<Record<string, StrategyVersion[]>>({});
  const [openId, setOpenId] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState("");
  const activeCount = (data?.strategies ?? []).filter((st) => st.status === "ACTIVE").length;

  const flash = (m: string) => {
    setToast(m);
    setTimeout(() => setToast(""), 4000);
  };

  const setParam = async (sid: string, variable: string, value: string) => {
    setBusy(`param:${variable}`);
    try {
      await api.patch(endpoints.strategyParams(sid), { variable, value });
      flash(`Saved - ${variable} = ${value} (new version created, old version kept)`);
      refresh();
    } catch (e: any) {
      flash(e.message || "Could not save setting");
    } finally {
      setBusy("");
    }
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

  const toggleStatus = async (st: StrategyDoc) => {
    const next = st.status === "ACTIVE" ? "PAUSED" : "ACTIVE";
    setBusy(`toggle:${st.id}`);
    try {
      await api.patch(endpoints.strategy(st.id), { status: next });
      flash(`${st.short_name}: ${next === "ACTIVE" ? "ON - generating signals" : "OFF - paused"}`);
      refresh();
    } catch (e: any) {
      flash(e.message || "Could not update strategy");
    } finally {
      setBusy("");
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

      {/* ---- signal engine: per-strategy switches (scales to N strategies) ---- */}
      <Glass className="mb-6">
        <div className="flex items-center justify-between">
          <Eyebrow>Signal engine</Eyebrow>
          <GlowDot tone={activeCount === 0 ? "warn" : "pos"} size={6} pulse={activeCount > 0} />
        </div>
        <p className="mt-1 text-[11.5px] leading-relaxed text-txt-low">
          Switch each strategy ON or OFF. Currently{" "}
          <span className={activeCount === 0 ? "font-semibold text-warn" : "font-semibold text-txt-hi"}>
            {activeCount === 0 ? "no strategy is generating signals" : `${activeCount} generating signals`}
          </span>.
        </p>
        <div className={`mt-4 space-y-2 ${busy.startsWith("toggle:") ? "pointer-events-none opacity-50" : ""}`}>
          {(data?.strategies ?? []).map((st) => {
            const retired = st.status === "DISABLED";
            return (
              <div key={st.id} className="flex items-center justify-between gap-3 rounded-2xl border border-[rgba(var(--warm-rgb),0.07)] bg-[rgba(var(--warm-rgb),0.035)] px-4 py-3">
                <div className="min-w-0">
                  <div className="truncate text-[12.5px] font-semibold text-txt-hi">{st.short_name}</div>
                  <div className="text-[10px] text-txt-faint">
                    {retired ? "Retired - signal history kept" : st.status === "ACTIVE" ? "Generating signals" : "Paused - no new signals"}
                  </div>
                </div>
                {retired ? (
                  <span className="shrink-0 rounded-full border border-[rgba(var(--warm-rgb),0.14)] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.14em] text-txt-faint">Retired</span>
                ) : (
                  <button
                    onClick={() => toggleStatus(st)}
                    disabled={busy.startsWith("toggle:")}
                    aria-pressed={st.status === "ACTIVE"}
                    className={`tap min-h-[38px] shrink-0 rounded-full px-5 text-[11px] font-bold uppercase tracking-[0.12em] ${st.status === "ACTIVE" ? "chip-on" : "chip-off"}`}
                  >
                    {st.status === "ACTIVE" ? "On" : "Off"}
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {/* ENTRY MODE - the new strategy's trigger, visible right here */}
        {(data?.strategies ?? [])
          .filter((st) => st.id === "strategy_1_vp_pivots" && st.active_params?.entry_mode)
          .map((st) => (
            <ModeControl key="entry-mode" sid={st.id} k="entry_mode"
                         v={String(st.active_params!.entry_mode)}
                         spec={(st.experiment_variables ?? {})["entry_mode"]}
                         busy={busy} onSet={setParam} />
          ))}

        <p className="mt-1 text-[10px] leading-relaxed text-txt-faint">
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
              <span className="mr-1 hidden shrink-0 items-center gap-1 rounded-full border border-[rgba(var(--warm-rgb),0.14)] px-2.5 py-1 text-[9.5px] font-bold uppercase tracking-[0.14em] text-txt-faint sm:flex">
                Settings
              </span>
              <ChevronDown size={16} className={`shrink-0 text-txt-faint transition-transform duration-500 ${openId === s.id ? "rotate-180" : ""}`} />
            </button>

            <div className={`grid transition-all duration-500 ease-out ${openId === s.id ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
              <div className="overflow-hidden">
                <div className="px-5 pb-6">
                  <p className="text-[12px] leading-relaxed text-txt-low">{s.description}</p>

                  {/* MODE settings - prominent, one tap to change */}
                  {Object.entries(s.active_params ?? {})
                    .filter(([k]) => (s.experiment_variables ?? {})[k]?.type === "select")
                    .map(([k, v]) => (
                      <ModeControl key={k} sid={s.id} k={k} v={String(v)}
                                   spec={(s.experiment_variables ?? {})[k]}
                                   busy={busy} onSet={setParam} />
                    ))}

                  {/* numeric parameters - click a value to edit */}
                  <div className="mt-1 grid grid-cols-2 gap-x-6 gap-y-2 rounded-2xl border border-[rgba(var(--warm-rgb),0.06)] bg-[rgba(var(--warm-rgb),0.035)] p-4">
                    {Object.entries(s.active_params ?? {})
                      .filter(([k]) => k !== "expire_bars" && (s.experiment_variables ?? {})[k]?.type !== "select")
                      .map(([k, v]) => (
                        <NumParam key={k} k={k} v={v} busy={busy}
                                  onSet={(sid, variable, value) => setParam(s.id, variable, value)} />
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
      <button
        onClick={() => flash("Strategies are code modules: drop a file under backend/app/strategies and it appears here automatically - the AI can never invent one.")}
        className="tap mt-4 flex w-full items-center gap-4 rounded-[24px] border border-dashed border-[rgba(var(--warm-rgb),0.12)] px-5 py-5 text-left text-txt-low transition hover:border-[rgba(var(--warm-rgb),0.20)] hover:text-txt-mid">
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

const MODE_HINTS: Record<string, Record<string, string>> = {
  entry_mode: {
    hvn_rejection: "Price wicks into a high-volume pivot level and closes back - fade the rejection.",
    breakout: "Price CLOSES through an active volume-backed level - trade the break.",
    poc_bounce: "Price taps the Point of Control and closes back in the bounce direction.",
  },
};

/** Prominent control for select-type settings (e.g. entry_mode).
 *  The user picks the strategy's trigger mode right on the card. */
function ModeControl({ sid, k, v, spec, busy, onSet }: {
  sid: string; k: string; v: string; spec: any;
  busy: string; onSet: (sid: string, variable: string, value: string) => void;
}) {
  const opts: string[] = spec?.options ?? [];
  const hint = MODE_HINTS[k]?.[String(v)];
  return (
    <div className="mb-4 rounded-2xl border border-[rgba(var(--warm-rgb),0.10)] bg-[rgba(var(--warm-rgb),0.045)] p-4">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-txt-faint">
          {spec?.description || k}
        </span>
        {busy === `param:${k}` && (
          <span className="h-2 w-2 animate-pulse rounded-full bg-[rgb(var(--p-rgb))]" />
        )}
      </div>
      <div className="seg mt-3 w-full justify-stretch">
        {opts.map((o) => (
          <button
            key={o}
            className={`flex-1 ${String(v) === o ? "seg-on" : ""}`}
            disabled={busy === `param:${k}`}
            onClick={() => onSet(sid, k, o)}
          >
            {o.replace(/_/g, " ")}
          </button>
        ))}
      </div>
      {hint && (
        <p className="mt-2.5 text-[11px] leading-relaxed text-txt-low">{hint}</p>
      )}
    </div>
  );
}

/** Inline-editable numeric setting: click the value, type, Enter/blur saves. */
function NumParam({ k, v, onSet, busy }: {
  k: string; v: any; onSet: (sid: string, variable: string, value: string) => void; busy: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(v));
  const commit = () => {
    setEditing(false);
    if (draft !== String(v)) onSet("", k, draft);
  };
  return (
    <div className="flex items-baseline justify-between">
      <span className="font-mono text-[10.5px] text-txt-faint">{k}</span>
      {editing ? (
        <input
          autoFocus
          className="input w-[92px] px-2 py-0.5 text-right text-[12px]"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") { setDraft(String(v)); setEditing(false); }
          }}
        />
      ) : (
        <button
          className="num rounded-lg px-1.5 py-0.5 text-[12px] font-medium text-txt-mid underline-offset-2 decoration-dotted hover:bg-[rgba(var(--warm-rgb),0.07)] hover:text-txt-hi"
          title="Click to edit"
          onClick={() => { setDraft(String(v)); setEditing(true); }}
        >
          {String(v)}
        </button>
      )}
    </div>
  );
}

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
