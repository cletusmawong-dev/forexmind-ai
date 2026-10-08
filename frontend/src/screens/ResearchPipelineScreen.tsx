/* RESEARCH PIPELINE dashboard (owner brief 2026-10-08, section 15).
 *
 * Surfaces the autonomous background research engine WITHOUT backend
 * complexity: candidates with stage/evidence/recommendation, the evidence
 * report behind each one, the controlled source registry, and the research
 * memory (what was already tested + rejected, and why).
 *
 * The engine runs itself in the background; the only button here nudges an
 * extra bounded tick. Nothing on this screen can make a strategy live -
 * READY_FOR_REVIEW routes through the EXISTING human-approval lifecycle.
 */
import { useState } from "react";
import { FlaskConical, Lock, CheckCircle2, XCircle, MinusCircle, HelpCircle } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { Divider, ErrorNote, Glass, Pill, SectionHeader, Spinner, PageHeader, TEXT } from "../components/ui";

/* ---- types (matches backend app/research/*) ---- */
type Rec = "REJECT" | "CONTINUE_RESEARCH" | "SHADOW" | "READY_FOR_REVIEW";
interface CandidateRow {
  id: string; name: string; stage: string; market: string; timeframe: string;
  source_id: string; source_url: string; evidence_tier: number;
  evidence_level?: string; recommendation?: Rec; recommendation_reason?: string;
  verified_trades?: number; shadow_trades?: number; updatedAt?: string;
}
interface Gate { gate: string; verdict: string; detail: string }
interface Report {
  source_evidence: { tier_name: string; source_claim?: string; disclaimer: string };
  rules?: { source?: any; extracted?: any; implemented?: any };
  historical_test?: any; out_of_sample?: Gate; walk_forward?: Gate;
  stress_testing?: Gate[]; parameter_stability?: Gate; generalization?: Gate;
  regime_analysis?: { detail?: string; regimes?: any; sessions?: any };
  probabilities?: any; shadow_results?: any; shadow_probabilities?: any;
  risks?: string[]; recommendation: Rec; recommendation_reason: string;
  evidence_level?: string; human_approval_required?: boolean;
}
interface MemoryRow { name: string; market: string; verdict: string; reason: string; at: string }

const STAGES = ["DISCOVERED", "EXTRACTED", "RECONSTRUCTED", "BACKTESTED",
  "SHADOW", "READY_FOR_REVIEW", "CONTINUE_RESEARCH", "REJECTED"];

function recPill(rec?: Rec) {
  if (rec === "READY_FOR_REVIEW")
    return <Pill tone="pos"><CheckCircle2 size={11} className="mr-1 inline" />Ready for your review</Pill>;
  if (rec === "SHADOW")
    return <Pill tone="neutral"><FlaskConical size={11} className="mr-1 inline" />Shadow testing</Pill>;
  if (rec === "CONTINUE_RESEARCH")
    return <Pill tone="warn"><MinusCircle size={11} className="mr-1 inline" />Keep researching</Pill>;
  if (rec === "REJECT")
    return <Pill tone="danger"><XCircle size={11} className="mr-1 inline" />Rejected</Pill>;
  return <Pill tone="neutral"><HelpCircle size={11} className="mr-1 inline" />In progress</Pill>;
}

function stagePill(stage: string) {
  const tone = stage === "REJECTED" ? "danger" : stage === "READY_FOR_REVIEW" ? "pos" : "neutral";
  return <Pill tone={tone as any}>{stage.replace(/_/g, " ").toLowerCase()}</Pill>;
}

function Metric({ label, value, sub }: { label: string; value: any; sub?: string }) {
  return (
    <div className="rounded-xl border border-[rgba(var(--p-rgb),0.14)] px-3 py-2">
      <p className={`text-[9px] uppercase tracking-[0.12em] ${TEXT.faint}`}>{label}</p>
      <p className={`text-[14px] font-semibold ${TEXT.headline}`}>{value ?? "—"}</p>
      {sub && <p className={`text-[9.5px] ${TEXT.hint}`}>{sub}</p>}
    </div>
  );
}

function gateColor(v: string) {
  return v === "PASS" ? "text-[var(--accent-green)]"
    : v === "FAIL" ? "text-[var(--accent-red)]"
    : v === "MARGINAL" ? "text-warn"
    : TEXT.hint;
}

function GateRow({ g }: { g: Gate }) {
  if (!g) return null;
  return (
    <div className="flex items-start justify-between gap-3 py-1.5">
      <div className="min-w-0">
        <p className={`text-[12px] font-semibold ${TEXT.body}`}>{g.gate.replace(/_/g, " ")}</p>
        <p className={`text-[10.5px] leading-snug ${TEXT.hint}`}>{g.detail}</p>
      </div>
      <span className={`shrink-0 text-[10.5px] font-bold uppercase ${gateColor(g.verdict)}`}>{g.verdict}</span>
    </div>
  );
}

function ApprovalActions({ id, onDone }: { id: string; onDone: () => void }) {
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const act = async (kind: "approve" | "reject") => {
    if (kind === "reject" && !note.trim()) { setErr("A rejection reason is required."); return; }
    setBusy(kind); setErr(null);
    try {
      await api.post(kind === "approve"
        ? endpoints.researchEngineApprove(id)
        : endpoints.researchEngineReject(id),
        { note });
      onDone();
    } catch (e: any) { setErr(e?.message || "action failed"); }
    finally { setBusy(null); }
  };
  return (
    <div className="space-y-2">
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Decision note (required to reject)"
        className="w-full rounded-xl border border-[rgba(var(--p-rgb),0.25)] bg-[rgba(var(--p-rgb),0.06)] px-3 py-2 text-[12px] text-txt-hi placeholder:text-txt-faint focus:outline-none" />
      <div className="flex gap-2">
        <button disabled={busy !== null} onClick={() => act("approve")}
          className="tap flex-1 rounded-lg border border-[rgba(var(--p-rgb),0.4)] bg-[rgba(var(--p-rgb),0.12)] px-3 py-2 text-[12px] font-bold text-[var(--accent-green)] disabled:opacity-60">
          {busy === "approve" ? "Approving…" : "Approve → immutable v1.0 (NOT live)"}
        </button>
        <button disabled={busy !== null} onClick={() => act("reject")}
          className="tap flex-1 rounded-lg border border-[rgba(var(--neg-rgb),0.4)] bg-[rgba(var(--neg-rgb),0.08)] px-3 py-2 text-[12px] font-bold text-[var(--accent-red)] disabled:opacity-60">
          {busy === "reject" ? "Rejecting…" : "Reject"}
        </button>
      </div>
      <ErrorNote message={err} onDismiss={() => setErr(null)} />
    </div>
  );
}

function ReportView({ report, candidateId, onDecision }: { report: Report; candidateId?: string; onDecision?: () => void }) {
  const m = report.historical_test || {};
  const p = report.probabilities || {};
  const gates: Gate[] = [
    report.out_of_sample, report.walk_forward, report.parameter_stability,
    ...(report.stress_testing || []), report.generalization,
  ].filter(Boolean) as Gate[];
  return (
    <div className="space-y-4">
      {/* the one rule: source claims are never verified results */}
      <div className="rounded-xl border border-[rgba(var(--amber-rgb),0.35)] bg-[rgba(var(--amber-rgb),0.07)] px-3 py-2.5">
        <p className={`text-[10px] font-bold uppercase tracking-[0.12em] ${TEXT.label}`}>
          Source claim ({report.source_evidence?.tier_name})</p>
        <p className={`mt-0.5 text-[12px] ${TEXT.body}`}>{report.source_evidence?.source_claim || "—"}</p>
        <p className={`mt-1 text-[10px] font-semibold ${TEXT.hint}`}>{report.source_evidence?.disclaimer}</p>
      </div>

      {m.trade_count !== undefined && (
        <>
          <SectionHeader>Verified backtest</SectionHeader>
          <div className="grid grid-cols-3 gap-2">
            <Metric label="Trades" value={m.trade_count} />
            <Metric label="Win rate" value={m.win_rate != null ? `${m.win_rate}%` : "—"} />
            <Metric label="Avg R" value={m.avg_r ?? "—"} />
            <Metric label="Profit factor" value={m.profit_factor ?? "—"} />
            <Metric label="Net R" value={m.net_r ?? "—"} />
            <Metric label="Max DD" value={m.max_drawdown_r != null ? `${m.max_drawdown_r}R` : "—"}
              sub={`worst streak ${m.max_losing_streak ?? "—"} losses`} />
          </div>
        </>
      )}

      {gates.length > 0 && (
        <>
          <SectionHeader>Robustness gates</SectionHeader>
          <div className="divide-y divide-[rgba(var(--warm-rgb),0.12)]">
            {gates.map((g, i) => <GateRow key={i} g={g} />)}
          </div>
        </>
      )}

      {report.regime_analysis?.detail && (
        <>
          <SectionHeader>Regime / session check</SectionHeader>
          <p className={`text-[12px] ${TEXT.body}`}>{report.regime_analysis.detail}</p>
          {report.regime_analysis.sessions && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(report.regime_analysis.sessions).map(([k, v]: [string, any]) => (
                <span key={k}
                  className={`rounded-full border border-[rgba(var(--p-rgb),0.18)] px-2 py-0.5 text-[10px] font-semibold ${typeof v === "object" && v?.P_win != null ? TEXT.body : TEXT.hint}`}>
                  {k}{typeof v === "object"
                    ? v.P_win != null
                      ? ` · ${Math.round(100 * v.P_win)}% of ${v.n}`
                      : ` · n=${v.n} (too few)`
                    : ""}
                </span>
              ))}
            </div>
          )}
        </>
      )}

      <SectionHeader>Probabilities (measured, not AI confidence)</SectionHeader>
      {p.P_win?.status === "INSUFFICIENT_DATA" || !p.P_win ? (
        <p className={`text-[12px] ${TEXT.hint}`}>
          {p.P_win?.status === "INSUFFICIENT_DATA"
            ? `Insufficient data — ${p.P_win.n} verified trades (need ≥ ${p.P_win.note?.match(/\d+/)?.[0] || 30}). No probabilities are invented.`
            : "No probability estimates yet."}
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {(["P_win", "P_SL", "P_TP1", "P_TP2", "P_TP3"] as const).map((k) => (
            <Metric key={k} label={k.replace("_", " (").concat(")")}
              value={p[k]?.percent != null ? `${p[k].percent}%` : "—"}
              sub={p[k]?.n != null ? `n=${p[k].n}` : undefined} />
          ))}
          <Metric label="Expected R" value={p.expected_R?.value ?? "—"}
            sub={p.expected_R?.n != null ? `n=${p.expected_R.n}` : undefined} />
        </div>
      )}

      {report.shadow_results && report.shadow_results.n > 0 && (
        <>
          <SectionHeader>Shadow results (hypothetical, never brokered)</SectionHeader>
          <div className="grid grid-cols-3 gap-2">
            <Metric label="Shadow trades" value={report.shadow_results.n} />
            <Metric label="Shadow avg R" value={report.shadow_results.avg_r ?? "—"} />
            <Metric label="Shadow win rate"
              value={report.shadow_results.win_rate != null ? `${report.shadow_results.win_rate}%` : "—"} />
          </div>
        </>
      )}

      {report.risks && report.risks.length > 0 && (
        <>
          <SectionHeader>Risks</SectionHeader>
          <ul className="space-y-1">
            {report.risks.map((r, i) => (
              <li key={i} className={`flex gap-1.5 text-[11.5px] leading-snug ${TEXT.body}`}>
                <span className="shrink-0">•</span><span>{r}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="rounded-xl border border-[rgba(var(--p-rgb),0.3)] bg-[rgba(var(--p-rgb),0.06)] px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <p className={`text-[10px] font-bold uppercase tracking-[0.12em] ${TEXT.label}`}>Recommendation</p>
          {recPill(report.recommendation)}
        </div>
        <p className={`mt-1 text-[12px] leading-snug ${TEXT.body}`}>{report.recommendation_reason}</p>
        {report.recommendation === "READY_FOR_REVIEW" && (
          <p className={`mt-1.5 flex items-center gap-1 text-[10.5px] font-semibold ${TEXT.hint}`}>
            <Lock size={11} /> Queued for HUMAN approval - the engine cannot take it live.
          </p>
        )}
        {report.recommendation === "READY_FOR_REVIEW" && candidateId && onDecision && (
          <div className="mt-2.5 border-t border-[rgba(var(--p-rgb),0.18)] pt-2.5">
            <ApprovalActions id={candidateId} onDone={onDecision} />
          </div>
        )}
      </div>
    </div>
  );
}

export function ResearchPipelineScreen() {
  const cands = usePolling<{ candidates: CandidateRow[] }>(
    () => api.get(endpoints.researchEngineCandidates), 12000);
  const sources = usePolling<{ sources: any[]; tiers: Record<string, string>; policy: string }>(
    () => api.get(endpoints.researchEngineSources), 60000);
  const memory = usePolling<{ total: number; rejected: number; recent: MemoryRow[] }>(
    () => api.get(endpoints.researchEngineMemory), 30000);
  const [openId, setOpenId] = useState<string | null>(null);
  const [report, setReport] = useState<Record<string, Report | "none">>({});
  const [tickBusy, setTickBusy] = useState(false);
  const [tickMsg, setTickMsg] = useState<string | null>(null);

  const loadReport = async (id: string) => {
    if (report[id]) return;
    try {
      const r = await api.get(endpoints.researchEngineReport(id));
      setReport((prev) => ({ ...prev, [id]: r.report }));
    } catch {
      setReport((prev) => ({ ...prev, [id]: "none" }));
    }
  };

  const runTick = async () => {
    setTickBusy(true);
    setTickMsg(null);
    try {
      const res = await api.post(endpoints.researchEngineTick, {});
      setTickMsg(`Tick done: ${res.discovered} discovered, ${res.advanced} advanced.`);
      cands.refresh();
      memory.refresh();
    } catch (e: any) {
      setTickMsg(e?.message || "tick failed");
    } finally {
      setTickBusy(false);
      setTimeout(() => setTickMsg(null), 5000);
    }
  };

  const rows = cands.data?.candidates || [];
  const byStage = STAGES.map((s) => ({ s, n: rows.filter((r) => r.stage === s).length }))
    .filter((x) => x.n > 0);
  const ready = rows.filter((r) => r.recommendation === "READY_FOR_REVIEW").length;

  return (
    <div className="space-y-5 pb-10">
      <PageHeader
        title="Research Pipeline"
        sub="The engine researches documented strategies on its own: source → rules → reconstruction → testing → validation → shadow → evidence. You review evidence; nothing goes live without your approval."
        icon={<FlaskConical size={22} />}
        tone="violet"
        right={
          <button
            onClick={runTick}
            disabled={tickBusy}
            className="tap rounded-lg border border-[rgba(var(--p-rgb),0.3)] px-2.5 py-1.5 text-[11px] font-semibold text-txt-mid hover:bg-[rgba(var(--p-rgb),0.08)] disabled:opacity-60">
            {tickBusy ? "Running…" : "Run a tick"}
          </button>
        }
      />
      <ErrorNote message={tickMsg && !tickBusy ? tickMsg : null} />
      {cands.error && <ErrorNote message={`Pipeline status unavailable: ${cands.error}`} />}

      {/* status strip */}
      <Glass>
        <div className="grid grid-cols-3 gap-2">
          <Metric label="Candidates" value={rows.length} sub={`${ready} ready for review`} />
          <Metric label="Never rerun" value={memory.data?.total ?? "—"}
            sub={`${memory.data?.rejected ?? 0} rejected & remembered`} />
          <Metric label="Registry" value={sources.data?.sources.length ?? "—"} sub="approved sources" />
        </div>
        {byStage.length > 0 && (
          <>
            <Divider />
            <div className="flex flex-wrap gap-1.5">
              {byStage.map(({ s, n }) => (
                <span key={s} className={`rounded-full border border-[rgba(var(--p-rgb),0.18)] px-2 py-0.5 text-[10px] font-semibold ${TEXT.hint}`}>
                  {s.replace(/_/g, " ").toLowerCase()} · {n}
                </span>
              ))}
            </div>
          </>
        )}
      </Glass>

      {/* READY FOR YOUR REVIEW first */}
      {ready > 0 && (
        <Glass>
          <SectionHeader>Waiting for your approval</SectionHeader>
          <p className={`text-[11.5px] ${TEXT.body}`}>
            {rows.filter((r) => r.recommendation === "READY_FOR_REVIEW")
              .map((r) => `${r.name} (${r.market} ${r.timeframe})`).join(", ")} - open the
            candidate below, read the evidence, then decide in Strategies → Approvals.
          </p>
        </Glass>
      )}

      {/* candidate list */}
      {cands.loading && !cands.data ? (
        <Spinner label="Loading pipeline…" />
      ) : rows.length === 0 ? (
        <Glass>
          <p className={`text-[12.5px] ${TEXT.body}`}>
            No candidates yet. The background engine discovers documented strategies
            automatically - the first tick runs within minutes of startup.
          </p>
        </Glass>
      ) : (
        <div className="space-y-3">
          {rows.map((c) => (
            <Glass key={c.id} level={2}>
              <button className="w-full text-left" onClick={() => { setOpenId(openId === c.id ? null : c.id); loadReport(c.id); }}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className={`truncate text-[14px] font-bold ${TEXT.headline}`}>{c.name}</p>
                    <p className={`mt-0.5 text-[11px] ${TEXT.hint}`}>
                      {c.market} · {c.timeframe} · tier {c.evidence_tier}
                      {c.verified_trades != null ? ` · ${c.verified_trades} verified trades` : ""}
                      {c.shadow_trades ? ` · ${c.shadow_trades} shadow` : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    {stagePill(c.stage)}
                    {recPill(c.recommendation)}
                  </div>
                </div>
                {c.recommendation_reason && (
                  <p className={`mt-1.5 line-clamp-2 text-[11px] leading-snug ${TEXT.hint}`}>{c.recommendation_reason}</p>
                )}
              </button>
              {openId === c.id && (
                <div className="mt-3 border-t border-[rgba(var(--warm-rgb),0.14)] pt-3">
                  {report[c.id] === "none" ? (
                    <p className={`text-[12px] ${TEXT.hint}`}>
                      Evidence report not ready - the engine is still working on this candidate.
                    </p>
                  ) : report[c.id] ? (
                    <ReportView report={report[c.id] as Report} candidateId={c.id}
                      onDecision={() => { setReport({}); cands.refresh(); memory.refresh(); }} />
                  ) : (
                    <Spinner label="Loading evidence…" />
                  )}
                  {c.source_url && (
                    <p className={`mt-3 break-all text-[10px] ${TEXT.faint}`}>Source: {c.source_url}</p>
                  )}
                </div>
              )}
            </Glass>
          ))}
        </div>
      )}

      {/* source registry */}
      {sources.data && (
        <Glass>
          <SectionHeader>Source registry</SectionHeader>
          <p className={`text-[11px] ${TEXT.hint}`}>{sources.data.policy}</p>
          <Divider />
          <div className="space-y-1.5">
            {(sources.data?.sources || []).map((s: any) => (
              <div key={s.id} className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className={`truncate text-[12px] font-semibold ${TEXT.body}`}>{s.name}</p>
                  {s.url && <p className={`truncate text-[10px] ${TEXT.faint}`}>{s.url}</p>}
                </div>
                <Pill tone={s.tier === 1 ? "pos" : s.tier === 2 ? "neutral" : "warn"}>
                  T{s.tier} {sources.data?.tiers[String(s.tier)]}
                </Pill>
              </div>
            ))}
          </div>
        </Glass>
      )}

      {/* research memory */}
      {memory.data && memory.data.total > 0 && (
        <Glass>
          <SectionHeader>Research memory</SectionHeader>
          <p className={`text-[11px] ${TEXT.hint}`}>
            {memory.data.total} experiments recorded, {memory.data.rejected} rejected with
            reasons - identical experiments are never rerun.
          </p>
          <Divider />
          <div className="space-y-2">
            {memory.data.recent.map((m, i) => (
              <div key={i}>
                <div className="flex items-center justify-between gap-2">
                  <p className={`truncate text-[12px] font-semibold ${TEXT.body}`}>{m.name} <span className={TEXT.faint}>· {m.market}</span></p>
                  <Pill tone={m.verdict === "REJECT" ? "danger" : m.verdict === "READY_FOR_REVIEW" ? "pos" : "neutral"}>
                    {m.verdict.replace(/_/g, " ").toLowerCase()}
                  </Pill>
                </div>
                <p className={`text-[10.5px] leading-snug ${TEXT.hint}`}>{m.reason}</p>
              </div>
            ))}
          </div>
        </Glass>
      )}
    </div>
  );
}
