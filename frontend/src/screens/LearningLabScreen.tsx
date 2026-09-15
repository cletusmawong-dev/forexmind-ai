import { useState } from "react";
import { ArrowRight, FlaskConical, GraduationCap, ShieldAlert } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Experiment, Hypothesis, Lesson, Observation, StrategyVersion } from "../lib/types";
import { DemoTag, Divider, Eyebrow, Glass, GlowDot, Pill, Segmented, Spinner } from "../components/ui";
import { ChevronDown } from "lucide-react";
import { fmtDateTime } from "../lib/format";

const FLOW = ["Trade", "Result", "Analysis", "Lesson", "Hypothesis", "1-Var Test", "Approval", "New version"];
const TABS = ["OBSERVATIONS", "QUEUE", "EXPERIMENTS", "PATTERNS", "HISTORY"] as const;

export function LearningLabScreen() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("OBSERVATIONS");
  const obs = usePolling<{ observations: Observation[] }>(() => api.get(endpoints.observations), 10000);
  const lessons = usePolling<{ lessons: Lesson[] }>(() => api.get(endpoints.lessons), 8000);
  const hyps = usePolling<{ hypotheses: Hypothesis[] }>(() => api.get(endpoints.hypotheses), 6000);
  const exps = usePolling<{ experiments: Experiment[] }>(() => api.get(endpoints.experiments), 8000);
  const versions2 = usePolling<{ versions: StrategyVersion[] }>(() => api.get(endpoints.strategyVersions("strategy_2_ema_atr")), 10000);
  const versions1 = usePolling<{ versions: StrategyVersion[] }>(() => api.get(endpoints.strategyVersions("strategy_1_zero_lag")), 10000);

  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState("");
  const [armedRollback, setArmedRollback] = useState<string>("");

  const flash = (m: string) => {
    setToast(m);
    setTimeout(() => setToast(""), 4200);
  };

  const decide = async (id: string, decision: "approve" | "reject") => {
    setBusy(id);
    try {
      await api.post(endpoints[decision](id));
      flash(decision === "approve" ? "Approved - a new strategy version was created." : "Rejected - the change was not applied.");
    } catch (e: any) {
      flash(e.message);
    } finally {
      setBusy("");
    }
  };

  const runExperiment = async (hypId: string) => {
    setBusy(hypId);
    try {
      const r = await api.post<{ experiment: Experiment }>(endpoints.runExperiment, { hypothesis_id: hypId });
      flash(`Experiment completed: ${r.experiment.result.replace(/_/g, " ").toLowerCase()}.`);
    } catch (e: any) {
      flash(e.message);
    } finally {
      setBusy("");
    }
  };

  const rollback = async (strategyId: string, version: string) => {
    const key = `${strategyId}:${version}`;
    if (armedRollback !== key) {
      setArmedRollback(key);
      setTimeout(() => setArmedRollback((k) => (k === key ? "" : k)), 4000);
      return;
    }
    setArmedRollback("");
    setBusy(key);
    try {
      await api.post(endpoints.rollback(strategyId), { target_version: version, confirm: true, reason: "User rollback from Learning Lab" });
      flash(`Rolled back to v${version} - history retained, change is logged.`);
    } catch (e: any) {
      flash(e.message);
    } finally {
      setBusy("");
    }
  };

  const allHyps = hyps.data?.hypotheses ?? [];
  const pendingCount = allHyps.filter((h) => h.status === "AWAITING_APPROVAL").length;
  const proposedCount = allHyps.filter((h) => h.status === "PROPOSED").length;
  const observations = obs.data?.observations ?? [];
  const factObs = observations.filter((o) => o.kind === "FACT");
  const patternObs = observations.filter((o) => o.kind === "POSSIBLE_EXPLANATION");
  const [openEvidence, setOpenEvidence] = useState<string | null>(null);
  const [openLesson, setOpenLesson] = useState<string | null>(null);
  const [splitOpen, setSplitOpen] = useState<string | null>(null);

  return (
    <div className="animate-fadeUp">
      <header className="mb-7 flex items-start justify-between">
        <div className="flex items-center gap-3.5">
          <span className="icon-chip" aria-hidden="true"><GraduationCap size={20} /></span>
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight">Learning Lab</h1>
          <p className="mt-1 text-[12.5px] text-txt-low">Learning from every completed signal.</p>
          </div>
        </div>
        <DemoTag />
      </header>

      {/* ---- the loop: thin lines, glowing nodes (desktop) ---- */}
      <Glass className="!py-5 max-lg:hidden" pad={false}>
        <div className="no-scrollbar overflow-x-auto px-6 lg:flex lg:justify-between lg:overflow-visible">
          <div className="flex flex-col items-start gap-0 lg:flex-row lg:items-center lg:gap-0 lg:w-full">
            {FLOW.map((step, i) => (
              <div key={step} className="flex items-start lg:flex-1 lg:flex-col lg:items-center">
                <div className="flex flex-col items-center">
                  <span
                    className={`h-[9px] w-[9px] rounded-full ${
                      i <= 2 ? "bg-acc" : i <= 4 ? "bg-acc-violet" : i === 6 ? "bg-warn" : "bg-pos"
                    }`}
                    style={{ boxShadow: `0 0 12px ${i <= 2 ? "rgba(108,158,255,.8)" : i <= 4 ? "rgba(167,155,247,.8)" : i === 6 ? "rgba(229,181,103,.8)" : "rgba(62,207,142,.8)"}` }}
                  />
                  <span className="mt-2 whitespace-nowrap text-[9px] font-semibold uppercase tracking-[0.14em] text-txt-low">{step}</span>
                </div>
                {i < FLOW.length - 1 && (
                  <>
                    <span className="my-1 h-6 w-px bg-gradient-to-b from-white/15 to-white/[0.04] lg:my-0 lg:h-px lg:w-auto lg:flex-1 lg:bg-gradient-to-r" />
                    <span className="w-6 lg:hidden" />
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      </Glass>

      {/* tabs */}
      <div className="no-scrollbar mt-7 overflow-x-auto">
        <Segmented
          className="min-w-[460px]"
          value={tab}
          onChange={(k) => setTab(k as any)}
          options={[
            { key: "OBSERVATIONS", label: `Insights` },
            { key: "QUEUE", label: pendingCount ? `Queue (${pendingCount})` : "Queue" },
            { key: "EXPERIMENTS", label: "Experiments" },
            { key: "PATTERNS", label: "Patterns" },
            { key: "HISTORY", label: "History" },
          ]}
        />
      </div>

      {toast && (
        <div className="glass-2 mt-5 flex items-center gap-2.5 px-4 py-3 text-[12px] font-medium text-acc-cyan animate-fadeUp">
          <GlowDot tone="acc" size={6} pulse={false} /> {toast}
        </div>
      )}

      {/* ---------------- AI OBSERVATIONS ---------------- */}
      {tab === "OBSERVATIONS" && (
        <div className="mt-5 space-y-4">
          <p className="px-1 text-[11px] leading-relaxed text-txt-faint">
            Every statement is labeled FACT, POSSIBLE EXPLANATION or UNTESTED HYPOTHESIS and shows its sample size. The AI never claims more than the data supports.
          </p>
          {obs.loading && !obs.data ? (
            <Spinner />
          ) : factObs.length === 0 ? (
            <Glass level={2} className="text-center">
              <p className="text-[13px] text-txt-mid">No observations yet.</p>
              <p className="mt-1 text-[11.5px] text-txt-faint">The engine records market regime and outcome data as signals complete. Statements appear once evidence exists.</p>
            </Glass>
          ) : (
            factObs.map((o, idx) => <ObsCard key={idx} o={o} open={openEvidence === o.text} onToggle={() => setOpenEvidence(openEvidence === o.text ? null : o.text)} />)
          )}
          {factObs.length === 0 && (obs.data?.observations ?? []).some((o) => o.kind !== "FACT") && (
            <p className="px-1 text-[11px] text-txt-faint">Anything not yet supported by enough data lives under Patterns or is withheld entirely.</p>
          )}
        </div>
      )}

      {/* ---------------- APPROVAL QUEUE ---------------- */}
      {tab === "QUEUE" && (
        <div className="mt-5 space-y-4">
          <p className="px-1 text-[11px] leading-relaxed text-txt-faint">
            The AI can never change a live strategy by itself. Approved changes become a new immutable version; nothing is overwritten.
          </p>
          {(hyps.loading && !hyps.data) ? (
            <Spinner />
          ) : allHyps.length === 0 ? (
            <Glass level={2} className="text-center">
              <p className="text-[13px] text-txt-mid">Queue is empty.</p>
              <p className="mt-1 text-[11.5px] text-txt-faint">Hypotheses grow from evidence-backed lessons - exactly one variable at a time.</p>
            </Glass>
          ) : (
            [...allHyps].sort((a, b) => rank(b) - rank(a)).map((h) => {
              const pending = h.status === "AWAITING_APPROVAL";
              return (
                <Glass
                  key={h.id}
                  className={`animate-fadeUp !p-6 ${pending ? "border-warn/25" : ""}`}
                  pad={false}
                >
                  <div className="p-6">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-2.5">
                        <GlowDot tone={pending ? "warn" : "acc"} size={6} pulse={pending} />
                        <span className={`text-[11px] font-semibold uppercase tracking-[0.16em] ${pending ? "text-warn" : "text-acc"}`}>{h.hypothesis_id}</span>
                      </span>
                      <Pill tone={pending ? "warn" : h.status === "APPROVED" ? "pos" : h.status === "REJECTED" ? "neg" : "neutral"}>{h.status.replace("_", " ")}</Pill>
                    </div>
                    <div className="mt-1 text-[11px] text-txt-faint">{h.strategy_name}</div>

                    {pending && (
                      <p className="mt-4 text-[12px] font-medium leading-relaxed text-warn/90">
                        AI discovered a possible improvement - awaiting your review.
                      </p>
                    )}

                    {/* the change, typographically */}
                    <div className="mt-5 flex items-center justify-center gap-5">
                      <div className="text-center">
                        <div className="eyebrow !text-[9px]">Current</div>
                        <div className="num mt-1.5 text-[30px] font-light text-txt-mid">{String(h.old_value)}</div>
                      </div>
                      <ArrowRight size={18} className="mt-3 text-acc-cyan" />
                      <div className="text-center">
                        <div className="eyebrow !text-[9px]">Proposed</div>
                        <div className="num mt-1.5 text-[30px] font-light text-txt-hi" style={{ textShadow: "0 0 24px rgba(124,213,242,0.4)" }}>{String(h.new_value)}</div>
                      </div>
                    </div>
                    <p className="mt-3 text-center text-[10.5px] text-txt-faint">
                      <span className="font-mono text-txt-low">{h.variable}</span> - only one variable changes - everything else stays identical
                    </p>

                    <p className="mt-4 text-[12px] leading-relaxed text-txt-low">
                      <span className="text-txt-mid">Reason - </span>{h.reason}
                    </p>

                    {h.result && (
                      <>
                        <Divider className="my-5" />
                        <div className="flex items-stretch justify-between gap-3 text-center">
                          <ResultStat label="Original" value={`${h.old_metrics?.win_rate ?? "-"}%`} sub={`${h.old_metrics?.trades ?? "-"} trades`} />
                          <ResultStat label="Experimental" value={`${h.new_metrics?.win_rate ?? "-"}%`} sub={`${h.new_metrics?.trades ?? "-"} trades`} tone="text-acc-cyan" />
                          <ResultStat
                            label="Verdict"
                            value={h.result === "INSUFFICIENT_DATA" ? "Insuff." : h.result.charAt(0) + h.result.slice(1).toLowerCase().replace(/_/g, " ")}
                            tone={h.result === "IMPROVED" ? "text-pos" : h.result === "WORSE" ? "text-neg" : "text-txt-mid"}
                          />
                        </div>
                        {h.ai_conclusion && <p className="mt-4 text-[11.5px] leading-relaxed text-txt-faint">{h.ai_conclusion}</p>}
                      </>
                    )}

                    {/* actions */}
                    {pending && (
                      <div className="mt-6 flex gap-2.5">
                        <button className="btn-approve flex-1" disabled={busy === h.id} onClick={() => decide(h.id, "approve")}>Approve</button>
                        <button className="btn-reject flex-1" disabled={busy === h.id} onClick={() => decide(h.id, "reject")}>Reject</button>
                      </div>
                    )}
                    {h.status === "PROPOSED" && (
                      <button className="btn-ghost mt-6 w-full" disabled={busy === h.id} onClick={() => runExperiment(h.id)}>
                        <FlaskConical size={14} /> Run one-variable experiment
                      </button>
                    )}
                  </div>
                </Glass>
              );
            })
          )}
        </div>
      )}

      {/* ---------------- EXPERIMENTS ---------------- */}
      {tab === "EXPERIMENTS" && (
        <div className="mt-5 space-y-4">
          <p className="px-1 text-[11px] leading-relaxed text-txt-faint">
            Each experiment compares original vs experimental on the same dataset - results are never fabricated. Verdicts never rest on win rate alone.
          </p>
          {exps.loading && !exps.data ? (
            <Spinner />
          ) : (exps.data?.experiments.length ?? 0) === 0 ? (
            <Glass level={2} className="text-center">
              <p className="text-[13px] text-txt-mid">No experiments yet.</p>
              <p className="mt-1 text-[11.5px] text-txt-faint">Run one from a proposed hypothesis in the Queue.</p>
            </Glass>
          ) : (
            exps.data!.experiments.map((e) => (
              <Glass key={e.id} pad={false} className="animate-fadeUp">
                <div className="flex items-center justify-between px-6 pt-5">
                  <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-acc">{e.experiment_code ?? e.hypothesis_id}</span>
                  <Pill tone={e.result === "IMPROVED" ? "pos" : e.result === "WORSE" ? "neg" : "neutral"}>
                    {e.status === "READY_FOR_REVIEW" && e.result === "INSUFFICIENT_DATA" ? "insufficient data" : e.result.replace(/_/g, " ")}
                  </Pill>
                </div>
                <p className="px-6 pt-1 text-[11px] text-txt-faint">
                  {e.strategy_id === "strategy_1_zero_lag" ? "Zero Lag Trend" : "9/21 EMA Smart TP/SL"} - <span className="font-mono">{e.variable}</span> {String(e.old_value)} {'->'} {String(e.new_value)} - {e.market} {e.timeframe}
                </p>

                {e.overfitting_risk && (
                  <div className="mx-6 mt-4 flex items-start gap-2.5 rounded-2xl border border-warn/30 bg-warn/[0.07] px-4 py-3">
                    <ShieldAlert size={15} className="mt-0.5 shrink-0 text-warn" />
                    <p className="text-[11.5px] font-medium leading-relaxed text-warn">
                      HIGH OVERFITTING RISK - improves the training period but degrades on the validation period. Do not approve without further out-of-sample evidence.
                    </p>
                  </div>
                )}

                <div className="mt-4 flex items-stretch divide-x divide-white/[0.05]">
                  <Evidence label="Win rate" value={`${e.original_metrics?.win_rate ?? "-"}%`} sub={`${e.experimental_metrics?.win_rate ?? "-"}%`} />
                  <Evidence label="Expectancy" value={`${e.original_metrics?.expectancy ?? "-"}R`} sub={`${e.experimental_metrics?.expectancy ?? "-"}R`} />
                  <Evidence label="Profit factor" value={e.original_metrics?.profit_factor ?? "-"} sub={e.experimental_metrics?.profit_factor ?? "-"} />
                  <Evidence label="Trades" value={e.original_metrics?.trades ?? "-"} sub={e.experimental_metrics?.trades ?? "-"} />
                </div>
                <p className="px-6 pb-4 pt-4 text-[11.5px] leading-relaxed text-txt-low">{e.conclusion}</p>

                {e.split && (
                  <>
                    <button className="tap w-full border-t border-white/[0.05] px-6 py-3 text-left" onClick={() => setSplitOpen(splitOpen === e.id ? null : e.id)}>
                      <span className="flex items-center justify-between text-[10.5px] font-semibold uppercase tracking-[0.14em] text-txt-low">
                        Train / validation split
                        <ChevronDown size={14} className={`transition-transform duration-300 ${splitOpen === e.id ? "rotate-180" : ""}`} />
                      </span>
                    </button>
                    {splitOpen === e.id && (
                      <div className="animate-fadeUp border-t border-white/[0.05] px-6 pb-5 pt-4">
                        <SplitTable title="Train (70%)" split={e.split.train} />
                        <div className="mt-4"><SplitTable title="Validation (30%)" split={e.split.validation} /></div>
                        <p className="mt-4 text-[10px] leading-relaxed text-txt-faint">Time-ordered split: the change is validated on the most recent 30% of trades it has never seen. Agreement across both periods lowers overfitting risk.</p>
                      </div>
                    )}
                  </>
                )}
                <div className="px-6 pb-5 pt-3 text-[9.5px] text-txt-faint">Same dataset for both versions - {fmtDateTime(e.createdAt)}</div>
              </Glass>
            ))
          )}
        </div>
      )}

      {/* ---------------- PATTERNS ---------------- */}
      {tab === "PATTERNS" && (
        <div className="mt-5 space-y-4">
          <p className="px-1 text-[11px] leading-relaxed text-txt-faint">
            Co-occurrences found by forensics across completed signals. A pattern is a possible explanation - never proof on its own.
          </p>
          {obs.loading && !obs.data ? (
            <Spinner />
          ) : patternObs.length === 0 ? (
            <Glass level={2} className="text-center">
              <p className="text-[13px] text-txt-mid">No patterns yet.</p>
              <p className="mt-1 text-[11.5px] text-txt-faint">Forensics compares each completed signal against similar past signals. Patterns appear only with repeat evidence.</p>
            </Glass>
          ) : (
            patternObs.map((o, idx) => <ObsCard key={idx} o={o} open={openEvidence === o.text} onToggle={() => setOpenEvidence(openEvidence === o.text ? null : o.text)} />)
          )}
        </div>
      )}

      {/* ---------------- HISTORY ---------------- */}
      {tab === "HISTORY" && (
        <div className="mt-5 space-y-8">
          {/* lessons */}
          <div>
            <Eyebrow className="mb-2">Lessons</Eyebrow>
            {lessons.loading && !lessons.data ? (
              <Spinner />
            ) : (lessons.data?.lessons.length ?? 0) === 0 ? (
              <Glass level={2} className="text-center">
                <p className="text-[13px] text-txt-mid">No lessons yet.</p>
                <p className="mt-1 text-[11.5px] text-txt-faint">Observations appear when enough completed signals support a pattern. Never conclusions without evidence.</p>
              </Glass>
            ) : (
              <div className="space-y-4">
                {lessons.data!.lessons.map((l, idx) => {
                  const expanded = openLesson ? openLesson === l.id : idx === 0;
                  return expanded ? (
                    <Glass key={l.id} className="animate-fadeUp" pad={false}>
                    <div className="flex items-center justify-between px-6 pt-5">
                      <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-acc-violet">Lesson {l.lesson_no}</span>
                      <Pill tone="neutral">{l.strategy_name}</Pill>
                    </div>
                    <p className="px-6 pt-3 text-[13px] leading-relaxed text-txt-hi">{l.observation}</p>
                    <div className="mt-4 flex items-stretch divide-x divide-white/[0.05] border-t border-white/[0.05]">
                      <EvCell label="Evidence" value={String(l.evidence)} />
                      <EvCell label="Win rate" value={`${l.win_rate}%`} delta={`vs ${l.baseline_win_rate}% base`} deltaTone={l.delta_pp >= 0 ? "text-pos" : "text-neg"} />
                      <EvCell label="Delta" value={`${l.delta_pp >= 0 ? "+" : ""}${l.delta_pp}pp`} mid />
                    </div>
                    </Glass>
                  ) : (
                    <button key={l.id} onClick={() => setOpenLesson(l.id)} className="glass glass-hover tap flex w-full items-center justify-between px-5 py-4 text-left">
                      <span className="text-[12.5px] text-txt-mid">Lesson {l.lesson_no} - {l.strategy_name}</span>
                      <ChevronDown size={15} className="rotate-180 text-txt-faint" />
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* versions + rollback */}
          {[
            { id: "strategy_1_zero_lag", title: "Strategy 1 - Zero Lag Trend", data: versions1.data?.versions },
            { id: "strategy_2_ema_atr", title: "Strategy 2 - 9/21 EMA Smart TP/SL", data: versions2.data?.versions },
          ].map((group) => (
            <div key={group.id}>
              <Eyebrow className="mb-2">{group.title}</Eyebrow>
              <Glass pad={false} className="divide-y divide-white/[0.05] !p-0">
                {(group.data ?? []).map((v) => (
                  <div key={v.id} className="flex items-center justify-between px-5 py-4">
                    <div className="flex items-center gap-3.5">
                      {v.active ? <GlowDot tone="pos" size={7} /> : <span className="h-[7px] w-[7px] rounded-full bg-white/15" />}
                      <div>
                        <div className="num text-[13.5px] font-semibold text-txt-hi">v{v.version}</div>
                        <div className="mt-0.5 text-[10.5px] text-txt-faint">{v.note || "Original version"}</div>
                      </div>
                    </div>
                    {v.active ? (
                      <Pill tone="pos">active</Pill>
                    ) : (
                      <button
                        className={`tap rounded-full border px-3.5 py-2 text-[10.5px] font-semibold transition-all duration-300 ${
                          armedRollback === `${group.id}:${v.version}`
                            ? "border-warn/50 bg-warn/10 text-warn"
                            : "border-white/[0.1] text-txt-low hover:border-white/20 hover:text-txt-mid"
                        }`}
                        disabled={busy === `${group.id}:${v.version}`}
                        onClick={() => rollback(group.id, v.version)}
                      >
                        {armedRollback === `${group.id}:${v.version}` ? "Confirm rollback?" : "Roll back"}
                      </button>
                    )}
                  </div>
                ))}
              </Glass>
            </div>
          ))}
          <p className="px-1 text-[10.5px] leading-relaxed text-txt-faint">
            Version history is never overwritten. Rolling back creates a logged event (who, when, why) and restores the previous parameters exactly.
          </p>
          <a href="/strategies" className="btn-ghost w-full">Compare versions in Strategy Manager</a>
        </div>
      )}
    </div>
  );
}

function rank(h: Hypothesis): number {
  if (h.status === "AWAITING_APPROVAL") return 3;
  if (h.status === "PROPOSED") return 2;
  return 1;
}

function kindTone(kind: string): { dot: string; text: string; pill: "pos" | "warn" | "neutral" } {
  if (kind === "FACT") return { dot: "bg-pos", text: "text-pos", pill: "pos" };
  if (kind === "POSSIBLE_EXPLANATION") return { dot: "bg-warn", text: "text-warn", pill: "warn" };
  return { dot: "bg-white/25", text: "text-txt-mid", pill: "neutral" };
}

function ObsCard({ o, open, onToggle }: { o: Observation; open: boolean; onToggle: () => void }) {
  const kt = kindTone(o.kind);
  return (
    <Glass pad={false} className="animate-fadeUp">
      <div className="p-6">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2.5">
            <span className={`h-[7px] w-[7px] rounded-full ${kt.dot}`} />
            <span className={`text-[10px] font-bold uppercase tracking-[0.16em] ${kt.text}`}>{o.kind.replace("_", " ")}</span>
          </span>
          <Pill tone={kt.pill}>{o.sample_size} signals</Pill>
        </div>
        <p className="mt-3 text-[13px] leading-relaxed text-txt-hi">{o.text}</p>
        {o.evidence && o.evidence.length > 0 && (
          <>
            <button className="tap mt-4 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-acc-cyan" onClick={onToggle}>
              {open ? "Hide evidence" : "View evidence"}
              <ChevronDown size={13} className={`transition-transform duration-300 ${open ? "rotate-180" : ""}`} />
            </button>
            {open && (
              <div className="animate-fadeUp mt-3 overflow-hidden rounded-2xl border border-white/[0.06]">
                <div className="divide-y divide-white/[0.05]">
                  {o.evidence.map((ev, i) => (
                    <div key={i} className="flex items-center justify-between bg-white/[0.02] px-4 py-2.5 text-[11px]">
                      <span className="font-mono text-txt-low">{ev.signal_id}</span>
                      <span className="flex items-center gap-3">
                        <span className="text-txt-faint">{ev.market} {ev.direction}</span>
                        <span className={ev.outcome === "WIN" ? "font-semibold text-pos" : ev.outcome === "LOSS" ? "font-semibold text-neg" : "text-txt-faint"}>
                          {ev.outcome ?? ev.status} {ev.r !== null && ev.r !== undefined ? `${ev.r > 0 ? "+" : ""}${ev.r}R` : ""}
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </Glass>
  );
}

function SplitTable({ title, split }: { title: string; split: { base: any; exp: any } }) {
  return (
    <div>
      <div className="eyebrow !text-[9px] mb-2">{title}</div>
      <div className="grid grid-cols-3 gap-2 text-center">
        <div />
        <div className="text-[9px] font-semibold uppercase tracking-[0.12em] text-txt-faint">Original</div>
        <div className="text-[9px] font-semibold uppercase tracking-[0.12em] text-[var(--accent-cyan)]">Experimental</div>
        <div className="text-left text-[10.5px] text-txt-low">Expectancy</div>
        <div className="num text-[12px] font-semibold">{split.base?.expectancy ?? "-"}R</div>
        <div className="num text-[12px] font-semibold text-[var(--accent-cyan)]">{split.exp?.expectancy ?? "-"}R</div>
        <div className="text-left text-[10.5px] text-txt-low">Win rate</div>
        <div className="num text-[12px] font-semibold">{split.base?.win_rate ?? "-"}%</div>
        <div className="num text-[12px] font-semibold text-[var(--accent-cyan)]">{split.exp?.win_rate ?? "-"}%</div>
        <div className="text-left text-[10.5px] text-txt-low">Trades</div>
        <div className="num text-[12px] text-txt-mid">{split.base?.trades ?? "-"}</div>
        <div className="num text-[12px] text-txt-mid">{split.exp?.trades ?? "-"}</div>
      </div>
    </div>
  );
}

function Evidence({ label, value, sub, suffix, tone = "text-white" }: { label: string; value: string; sub?: string; suffix?: string; tone?: string }) {
  return (
    <div className="flex-1 px-4 py-4 text-center">
      <div className="text-[8px] font-semibold uppercase tracking-[0.13em] text-txt-faint">{label}</div>
      <div className={`num mt-1.5 text-[14.5px] font-semibold ${tone}`}>
        {value} {suffix && <span className="text-[8.5px] font-medium text-txt-faint">{suffix}</span>}
      </div>
      {sub !== undefined && <div className="num mt-0.5 text-[10.5px] font-semibold text-[var(--accent-cyan)]">{sub}</div>}
    </div>
  );
}

function EvCell({ label, value, suffix, delta, deltaTone, mid }: { label: string; value: string; suffix?: string; delta?: string; deltaTone?: string; mid?: boolean }) {
  return (
    <div className={`flex-1 px-3 py-4 text-center ${mid ? "border-x border-white/[0.06]" : ""}`}>
      <div className="text-[8px] font-semibold uppercase tracking-[0.13em] text-txt-faint">{label}</div>
      <div className="num mt-1.5 text-[15px] font-semibold text-white">
        {value} {suffix && <span className="text-[8.5px] font-medium text-txt-faint">{suffix}</span>}
      </div>
      {delta && <div className={`num mt-0.5 text-[10.5px] font-semibold ${deltaTone}`}>{delta}</div>}
    </div>
  );
}

function ResultStat({ label, value, sub, tone = "text-txt-hi" }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="flex-1">
      <div className="eyebrow !text-[9px]">{label}</div>
      <div className={`num mt-1 text-[19px] font-light ${tone}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[9.5px] text-txt-faint">{sub}</div>}
    </div>
  );
}
