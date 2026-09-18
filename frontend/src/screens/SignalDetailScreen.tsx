import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ChevronDown, CircleSlash, FlaskConical, NotebookPen } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Candle, ReplayData, Signal } from "../lib/types";
import { DemoTag, Divider, Glass, MetricGrid, Pill, Spinner, StatusDot, ProgressBar } from "../components/ui";
import { CandleChart, ChartLine } from "../components/CandleChart";
import { PositionSizeCard } from "../components/PositionSizeCard";
import { fmtPrice, fmtR, fmtDateTime, TREND_LABEL } from "../lib/format";

export function SignalDetailScreen() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [whyOpen, setWhyOpen] = useState(false);
  const [analysisOpen, setAnalysisOpen] = useState(true);
  const [mode, setMode] = useState<null | "entered" | "skipped">(null);
  const [entryPrice, setEntryPrice] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualResult, setManualResult] = useState<"" | "WIN" | "LOSS" | "BREAK_EVEN" | "OPEN">("");
  const [manualPl, setManualPl] = useState("");
  const [error, setError] = useState("");

  const { data } = usePolling<{ signal: Signal }>(() => api.get(endpoints.signal(id!)), 5000);
  const s = data?.signal;
  const candlesQ = usePolling<{ candles: Candle[] }>(
    async () => (s ? api.get(`/api/markets/${s.market}/candles?tf=${s.timeframe}&limit=140`) : { candles: [] }),
    15000,
    [s?.market, s?.timeframe]
  );
  const [replayOpen, setReplayOpen] = useState(false);
  const replayQ = usePolling<ReplayData | null>(
    async () => (replayOpen && s ? api.get<ReplayData>(endpoints.signalReplay(s.id)) : null),
    60000,
    [s?.id, replayOpen]
  );
  const r = replayQ.data;

  if (!s) return <Spinner label="Loading signal..." />;
  const buy = s.direction === "BUY";

  const lines: ChartLine[] = [
    { price: s.sl, color: "#FB4D6A", label: "SL", style: "dashed", fade: 0.7 },
    { price: s.entry, color: "#4D7CFE", label: "Entry", style: "solid", fade: 0.9 },
    ...(s.tp1 ? [{ price: s.tp1, color: "#2FD98A", label: "TP1", style: "dashed" as const, fade: 0.8 }] : []),
    ...(s.tp2 ? [{ price: s.tp2, color: "#2FD98A", label: "TP2", style: "dashed" as const, fade: 0.55 }] : []),
    ...(s.tp3 ? [{ price: s.tp3, color: "#2FD98A", label: "TP3", style: "dashed" as const, fade: 0.35 }] : []),
  ];

  const act = async (action: "entered" | "skipped") => {
    setBusy(true);
    setError("");
    try {
      await api.post(endpoints.action(s.id), {
        action,
        entry_price: action === "entered" && entryPrice ? parseFloat(entryPrice) : undefined,
        notes,
      });
      setMode(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const firstCheck = s.checks?.[0];
  const tpHits = s.tp_hits ?? 0;

  return (
    <div className="anim-fadeUp">
      <header className="mb-5 flex items-center justify-between">
        <button onClick={() => navigate(-1)} className="tap -ml-2 flex items-center gap-1.5 rounded-full p-2 text-[13px] font-semibold text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]">
          <ArrowLeft size={17} /> Signals
        </button>
        <DemoTag />
      </header>

      {/* ---- title ---- */}
      <div className="flex items-start justify-between px-1">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-[28px] font-bold tracking-[-0.02em]">{s.market}</h1>
            <span
              className="flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[11px] font-bold tracking-[0.1em]"
              style={{
                borderColor: buy ? "rgba(var(--p-rgb),0.4)" : "rgba(var(--neg-rgb),0.4)",
                color: buy ? "var(--accent-green)" : "var(--accent-red)",
                background: buy ? "rgba(var(--p-rgb),0.1)" : "rgba(var(--neg-rgb),0.1)",
                boxShadow: buy ? "0 0 22px rgba(var(--p-rgb),0.2)" : "0 0 22px rgba(var(--neg-rgb),0.2)",
              }}
            >
              <StatusDot tone={buy ? "green" : "red"} size={6} pulse={false} />
              {s.direction}
            </span>
          </div>
          <p className="mt-2 text-[12px] text-[var(--text-secondary)]">
            {s.strategy_name} <span className="text-[var(--text-muted)]">- v{s.strategy_version} - {s.timeframe} - {fmtDateTime(s.candle_time)}</span>
          </p>
        </div>
        <div className="text-right">
          <div className="eyebrow">Score</div>
          <div className="num text-[24px] font-light">{s.score}</div>
        </div>
      </div>

      {/* ---- chart centerpiece ---- */}
      <Glass className="mt-6 !px-2 !py-3">
        <CandleChart candles={candlesQ.data?.candles ?? []} lines={lines} entry={s.entry} sl={s.sl} tp1={s.tp1 ?? undefined} markerTime={s.candle_time} markerDirection={s.direction} height={s.tp3 ? 340 : 300} />
      </Glass>

      {/* ---- levels ladder ---- */}
      <Glass className="mt-4 !py-1" pad={false}>
        <div className="px-5">
          <LadderRow label="Entry" value={fmtPrice(s.entry)} tone="text-[#1b69b8]" note={s.entry_zone && s.entry_zone[0] !== s.entry_zone[1] ? `zone ${fmtPrice(s.entry_zone[0])}-${fmtPrice(s.entry_zone[1])}` : undefined} />
          <Divider />
          <LadderRow label="Stop loss" value={fmtPrice(s.sl)} tone="text-[var(--accent-red)]" note={`risk ${fmtPrice(s.risk)}`} />
          <Divider />
          <LadderRow label="TP1" value={fmtPrice(s.tp1)} tone="text-[var(--accent-green)]" note="1R" muted={!!s.completed && tpHits < 1} hit={tpHits >= 1} />
          <Divider />
          {s.tp2 && <LadderRow label="TP2" value={fmtPrice(s.tp2)} tone="text-[var(--accent-green)]" note="2R" muted={!!s.completed && tpHits < 2} hit={tpHits >= 2} />}
          {s.tp2 && <Divider />}
          {s.tp3 && <LadderRow label="TP3" value={fmtPrice(s.tp3)} tone="text-[var(--accent-green)]" note="3R" muted={!!s.completed && tpHits < 3} hit={tpHits >= 3} />}
          {s.tp3 && <Divider />}
          <LadderRow label="Risk / reward" value={`1 : ${s.rr_primary}`} tone="" note={s.completed ? fmtR(s.r_multiple) : s.status.replace("_", " ")} />
        </div>
      </Glass>

      {/* ---- MT5 position size ---- */}
      <PositionSizeCard signal={s} />

      {/* ---- MTF ---- */}
      {s.mtf && (
        <div className="mt-4 flex flex-wrap items-center gap-2 px-1">
          <span className="eyebrow mr-1">MTF</span>
          {Object.entries(s.mtf).map(([tf, v]) => (
            <span
              key={tf}
              className={`rounded-full border border-[rgba(var(--warm-rgb),0.09)] bg-[rgba(var(--warm-rgb),0.055)] px-3 py-1.5 text-[10px] font-semibold ${
                v === 1 ? "text-[var(--accent-green)]" : v === -1 ? "text-[var(--accent-red)]" : "text-[var(--text-muted)]"
              }`}
            >
              {tf} <span className="font-bold">{TREND_LABEL[v]?.slice(0, 4) ?? "-"}</span>
            </span>
          ))}
        </div>
      )}

      {/* ---- why it qualifies ---- */}
      <div className="mt-4">
        <button onClick={() => setWhyOpen(!whyOpen)} className="glass glass-hover tap w-full px-5 py-4 text-left">
          <div className="flex items-center justify-between">
            <span className="text-[13.5px] font-semibold">Why it qualifies</span>
            <ChevronDown size={16} className={`text-[var(--text-muted)] transition-transform duration-500 ${whyOpen ? "rotate-180" : ""}`} />
          </div>
          {!whyOpen && firstCheck && <p className="mt-1.5 truncate text-[12px] text-[var(--accent-green)]">[ok] {firstCheck.label}</p>}
        </button>
        <div className={`grid transition-all duration-500 ease-out ${whyOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
          <div className="overflow-hidden">
            <div className="glass mt-2 space-y-4 px-5 py-5">
              {(s.checks ?? []).map((c, i) => (
                <div key={i}>
                  <div className={`flex items-start gap-2.5 text-[12.5px] ${c.ok ? "" : "text-[var(--text-muted)]"}`}>
                    <span className="text-[var(--accent-green)]">[ok]</span>
                    <span className="font-semibold text-[var(--text-primary)]">{c.label}</span>
                  </div>
                  <div className="ml-[22px] mt-1 text-[11px] text-[var(--text-muted)]">{c.detail}</div>
                </div>
              ))}
              <Divider />
              <div>
                <div className="eyebrow mb-2">AI analysis</div>
                <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{s.reason}</p>
              </div>
              <div>
                <div className="eyebrow mb-3">Score composition</div>
                <div className="space-y-3">
                  {Object.entries(s.score_components || {}).map(([k, v]) => (
                    <div key={k}>
                      <div className="mb-1.5 flex justify-between text-[10.5px] text-[var(--text-secondary)]">
                        <span>{k}</span>
                        <span className="num font-semibold">+{v}</span>
                      </div>
                      <ProgressBar pct={(v / 40) * 100} />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {s.extra_signal && (
        <Glass className="mt-4 border-[var(--accent-amber)]">
          <div className="flex items-start gap-2.5">
            <StatusDot tone="amber" size={7} pulse={false} />
            <div>
              <div className="text-[12.5px] font-bold text-[var(--accent-amber)]">
                EXTRA SIGNAL - MANUAL ONLY
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-txt-faint">
                Generated normally by the strategy, but automatic entry is disabled:
                {s.entry_blocked_reason || "daily protection active"}.
                Today's P/L at signal time: {s.daily_pl_at_signal != null ? `${s.daily_pl_at_signal > 0 ? "+" : ""}${s.daily_pl_at_signal}$` : "n/a"}.
              </p>
            </div>
          </div>
        </Glass>
      )}

      {s.extra_signal && !s.user_manual?.taken && (
        <Glass className="mt-4">
          <div className="eyebrow mb-3">Manual tracking</div>
          {!manualOpen ? (
            <button className="btn-ghost w-full" onClick={() => setManualOpen(true)}>
              I took this trade manually
            </button>
          ) : (
            <div>
              <div className="grid grid-cols-4 gap-2">
                {(["WIN", "LOSS", "BREAK_EVEN", "OPEN"] as const).map((r) => (
                  <button key={r} onClick={() => setManualResult(r)} aria-pressed={manualResult === r}
                    className={`tap min-h-[40px] rounded-xl border px-1 py-2 text-[10px] font-bold ${manualResult === r ? "chip-on" : "chip-off"}`}>
                    {r === "BREAK_EVEN" ? "BE" : r}
                  </button>
                ))}
              </div>
              <input
                className="input mt-3 w-full"
                placeholder="Manual P/L in $ (e.g. 85.50)"
                inputMode="decimal"
                value={manualPl}
                onChange={(e) => setManualPl(e.target.value)}
              />
              <div className="mt-3 flex gap-2">
                <button
                  className="btn-primary flex-1"
                  disabled={busy || !manualResult}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await api.post(endpoints.manualResult(s.id), {
                        taken: true,
                        result: manualResult,
                        pl: manualPl ? parseFloat(manualPl) : undefined,
                      });
                      setManualOpen(false);
                    } catch (e: any) {
                      setError(e.message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Record manual result
                </button>
                <button className="btn-ghost" onClick={() => setManualOpen(false)}>Cancel</button>
              </div>
            </div>
          )}
        </Glass>
      )}
      {s.extra_signal && s.user_manual?.taken && (
        <Glass className="mt-4">
          <div className="eyebrow mb-2">Manual tracking</div>
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-txt-mid">
            <StatusDot tone="green" size={6} pulse={false} />
            You took this manually -
            <span className="num font-semibold">{s.user_manual.result || "OPEN"}</span>
            {s.user_manual.pl != null && (
              <span className="num font-semibold">{s.user_manual.pl > 0 ? "+" : ""}{s.user_manual.pl}$</span>
            )}
            <span className="text-[10.5px] text-txt-faint">(tracked separately from automatic execution)</span>
          </div>
        </Glass>
      )}

      {/* ---- your decision ---- */}
      <Glass className="mt-4">
        <div className="eyebrow mb-3">Your decision</div>
        {!s.user_action && !mode && (
          <div>
          <p className="mb-3 px-1 text-[11px] leading-relaxed text-txt-faint">
            This signal is <span className="font-semibold text-txt-mid">always tracked to TP/SL</span> -
            confirming only records it as <span className="italic">your</span> trade in the journal.
          </p>
            <p className="mb-4 text-[12.5px] text-[var(--text-secondary)]">Did you enter this trade on MT5?</p>
            <div className="flex gap-2.5">
              <button className="btn-primary flex-1" onClick={() => setMode("entered")}>
                Yes, I entered
              </button>
              <button className="btn-ghost flex-1" onClick={() => setMode("skipped")}>
                I skipped
              </button>
            </div>
          </div>
        )}
        {mode === "entered" && (
          <div className="space-y-2.5 anim-fadeUp">
            <input className="input" placeholder={`Entry price - default ${fmtPrice(s.entry)}`} inputMode="decimal" value={entryPrice} onChange={(e) => setEntryPrice(e.target.value)} />
            <input className="input" placeholder="Notes (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} />
            <div className="flex gap-2.5">
              <button className="btn-primary flex-1" disabled={busy} onClick={() => act("entered")}>
                Record entry
              </button>
              <button className="btn-ghost" onClick={() => setMode(null)}>
                Cancel
              </button>
            </div>
          </div>
        )}
        {mode === "skipped" && (
          <div className="space-y-2.5 anim-fadeUp">
            <input className="input" placeholder="Why did you skip? (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} />
            <div className="flex gap-2.5">
              <button className="btn-ghost flex-1" disabled={busy} onClick={() => act("skipped")}>
                <CircleSlash size={13} /> Confirm skip
              </button>
              <button className="btn-ghost" onClick={() => setMode(null)}>
                Cancel
              </button>
            </div>
          </div>
        )}
        {s.user_action === "entered" && (
          <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-[var(--accent-cyan)]">
            <StatusDot tone="cyan" size={6} pulse={false} />
            Entered at <span className="num font-semibold">{fmtPrice(s.user_entry_price ?? s.entry)}</span>
            <span className="text-[var(--text-muted)]">- your result is tracked separately from the signal's.</span>
          </div>
        )}
        {s.user_action === "skipped" && (
          <div className="flex items-center gap-2.5 text-[12.5px] text-[var(--text-secondary)]">
            <CircleSlash size={13} /> Skipped - the agent still follows this signal to learn from it.
          </div>
        )}
        {error && <div className="mt-3 text-[11.5px] text-[var(--accent-red)]">{error}</div>}
      </Glass>

      {/* ---- result analysis ---- */}
      {s.result_analysis && (
        <div className="mt-4">
          <button onClick={() => setAnalysisOpen(!analysisOpen)} className="glass glass-hover tap flex w-full items-center justify-between px-5 py-4">
            <span className="flex items-center gap-2 text-[13.5px] font-semibold">
              <NotebookPen size={14} className="text-[var(--c-negdeep)]" /> Result analysis
            </span>
            <ChevronDown size={16} className={`text-[var(--text-muted)] transition-transform duration-500 ${analysisOpen ? "rotate-180" : ""}`} />
          </button>
          <div className={`grid transition-all duration-500 ease-out ${analysisOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
            <div className="overflow-hidden">
              <div className="glass mt-2 px-5 py-5">
                <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{s.result_analysis.what_happened}</p>
                <div className="mt-3 space-y-1">
                  {(s.result_analysis.setup_conditions ?? []).map((c: string, i: number) => (
                    <div key={i} className="text-[11px] text-[var(--text-muted)]">- {c}</div>
                  ))}
                </div>
                <Divider className="my-4" />
                <p className="text-[11px] leading-relaxed text-[var(--text-muted)]">{s.result_analysis.isolated_or_pattern}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ---- forensic replay ---- */}
      <div className="mt-4">
        <button onClick={() => setReplayOpen(!replayOpen)} className="glass glass-hover tap flex w-full items-center justify-between px-5 py-4">
          <span className="flex items-center gap-2 text-[13.5px] font-semibold">
            <FlaskConical size={14} className="text-acc-cyan" /> Forensic replay
            {s.completed && <span className="text-[10px] font-medium text-txt-faint">- what the agent saw, and what happened</span>}
          </span>
          <ChevronDown size={16} className={`text-[var(--text-muted)] transition-transform duration-500 ${replayOpen ? "rotate-180" : ""}`} />
        </button>
        {replayOpen && (
          <div className="animate-fadeUp mt-2 space-y-4">
            {!r ? (
              <Spinner label="Loading replay..." />
            ) : (
              <>
                <Glass className="!px-2 !py-3">
                  <CandleChart
                    candles={(r.candles ?? []).map((c) => ({
                      time: new Date(c.ts * 1000).toISOString().slice(0, 16),
                      open: c.open, high: c.high, low: c.low, close: c.close, volume: 0,
                    }))}
                    lines={[
                      { price: r.markers.sl, color: "#FB4D6A", label: "SL", style: "dashed", fade: 0.7 },
                      { price: r.markers.entry, color: "#4D7CFE", label: "Entry", style: "solid", fade: 0.9 },
                      ...(r.markers.tp1 ? [{ price: r.markers.tp1, color: "#2FD98A", label: "TP1", style: "dashed" as const, fade: 0.8 }] : []),
                      ...(r.markers.tp2 ? [{ price: r.markers.tp2, color: "#2FD98A", label: "TP2", style: "dashed" as const, fade: 0.55 }] : []),
                      ...(r.markers.tp3 ? [{ price: r.markers.tp3, color: "#2FD98A", label: "TP3", style: "dashed" as const, fade: 0.35 }] : []),
                    ]}
                    entry={r.markers.entry}
                    sl={r.markers.sl}
                    tp1={r.markers.tp1 ?? undefined}
                    markerTime={r.signal.candle_time}
                    markerDirection={r.signal.direction}
                    showLastPrice={false}
                    height={300}
                  />
                  <p className="px-4 pt-2 text-[10px] text-txt-faint">
                    Stored candles replayed from the signal time. {r.signal.outcome ? `Outcome: ${r.signal.outcome}${r.signal.r_multiple !== null && r.signal.r_multiple !== undefined ? ` (${r.signal.r_multiple > 0 ? "+" : ""}${r.signal.r_multiple}R)` : ""}.` : "Still tracking - outcome pending."}
                  </p>
                </Glass>
                {r.note && (
                  <p className="px-1 text-[10.5px] leading-relaxed text-txt-faint">{r.note}</p>
                )}
                {r.dna ? (
                  <Glass>
                    <div className="eyebrow mb-3">Signal DNA - captured at signal time</div>
                    <div className="flex flex-wrap gap-2">
                      {r.dna.regime && <Pill tone="neutral">{r.dna.regime.replace(/_/g, " ")}</Pill>}
                      {r.dna.regime_confidence !== undefined && <Pill tone="neutral">confidence {Math.round(r.dna.regime_confidence * 100)}%</Pill>}
                      {r.dna.volatility && <Pill tone="neutral">{r.dna.volatility} vol</Pill>}
                      {r.dna.atr14 !== undefined && <Pill tone="neutral">ATR {fmtPrice(r.dna.atr14)}</Pill>}
                      {r.dna.session && <Pill tone="neutral">{r.dna.session}</Pill>}
                      {r.dna.mtf_alignment && <Pill tone="neutral">MTF {r.dna.mtf_alignment}</Pill>}
                      {r.dna.momentum && <Pill tone="neutral">momentum {r.dna.momentum}</Pill>}
                      {r.dna.news_event && <Pill tone="warn">news: {r.dna.news_event}</Pill>}
                    </div>
                  </Glass>
                ) : (
                  <p className="px-1 text-[10.5px] text-txt-faint">No DNA snapshot for this signal - it predates DNA capture.</p>
                )}
                {r.forensics && r.forensics.findings.length > 0 && (
                  <Glass>
                    <div className="eyebrow mb-3">Forensic notes</div>
                    <div className="space-y-3">
                      {r.forensics.findings.map((f, i) => (
                        <div key={i}>
                          <div className="flex items-center gap-2">
                            <span className={`text-[9px] font-bold uppercase tracking-[0.14em] ${f.kind === "FACT" ? "text-pos" : f.kind === "POSSIBLE_EXPLANATION" ? "text-warn" : "text-txt-mid"}`}>{f.kind.replace("_", " ")}</span>
                            <span className="text-[12px] font-semibold text-txt-hi">{f.label}</span>
                          </div>
                          <p className="mt-1 text-[11.5px] leading-relaxed text-txt-low">{f.detail}</p>
                        </div>
                      ))}
                      {r.forensics.sample_size < 10 && (
                        <p className="text-[10.5px] text-txt-faint">Insufficient sample: only {r.forensics.sample_size} comparable completed signal(s) so far.</p>
                      )}
                    </div>
                  </Glass>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* ---- adaptive quality (historical evidence) ---- */}
      {s.adaptive ? (
        <Glass className="mt-4">
          <div className="flex items-start justify-between">
            <div>
              <div className="eyebrow">Adaptive Quality</div>
              <p className="mt-1 text-[11px] text-txt-faint">{s.adaptive.note}</p>
            </div>
            <div className="text-right">
              <div className={`num text-[34px] font-light leading-none ${s.adaptive.score >= 75 ? "text-pos" : s.adaptive.score >= 55 ? "text-warn" : "text-neg"}`}
                   style={{ textShadow: s.adaptive.score >= 75 ? "0 0 28px rgba(62,207,142,0.35)" : "none" }}>
                {s.adaptive.score}<span className="text-[14px] text-txt-faint">/100</span>
              </div>
              <div className="mt-1 text-[9px] font-semibold uppercase tracking-[0.14em] text-txt-faint">
                {s.adaptive.verdict} - {s.adaptive.reliability} reliability
              </div>
            </div>
          </div>

          <Divider className="my-4" />

          {/* WHY THIS SIGNAL SCORES THIS HIGH */}
          <div className="eyebrow mb-2.5">Why this signal scores this high</div>
          <div className="space-y-1.5">
            {s.adaptive.checks.map((c, i) => (
              <div key={i} className="flex items-start gap-2 text-[11.5px] leading-relaxed text-txt-low">
                <span className="mt-0.5 shrink-0 font-bold text-pos">[ok]</span>{c.label}
              </div>
            ))}
          </div>

          {/* Historical match */}
          <Divider className="my-4" />
          <div className="flex items-center justify-between">
            <div className="eyebrow">Historical match</div>
            {s.adaptive.historical.status === "OK" ? (
              <span className="text-[9px] font-semibold uppercase tracking-[0.12em] text-acc-cyan">
                {s.adaptive.historical.similar_signals} comparable signals
              </span>
            ) : (
              <span className="text-[9px] font-semibold uppercase tracking-[0.12em] text-warn">Insufficient sample</span>
            )}
          </div>
          {s.adaptive.historical.status === "OK" ? (
            <div className="mt-3 flex items-stretch divide-x divide-[rgba(var(--warm-rgb),0.06)] rounded-2xl border border-[rgba(var(--warm-rgb),0.06)] bg-[rgba(var(--warm-rgb),0.035)]">
              <div className="flex-1 py-3 text-center">
                <div className="num text-[16px] font-semibold text-pos">{s.adaptive.historical.wins}</div>
                <div className="mt-0.5 text-[9px] uppercase tracking-[0.12em] text-txt-faint">wins</div>
              </div>
              <div className="flex-1 py-3 text-center">
                <div className="num text-[16px] font-semibold text-neg">{s.adaptive.historical.losses}</div>
                <div className="mt-0.5 text-[9px] uppercase tracking-[0.12em] text-txt-faint">losses</div>
              </div>
              <div className="flex-1 py-3 text-center">
                <div className="num text-[16px] font-semibold text-txt-hi">{s.adaptive.historical.win_rate}%</div>
                <div className="mt-0.5 text-[9px] uppercase tracking-[0.12em] text-txt-faint">win rate</div>
              </div>
              <div className="flex-1 py-3 text-center">
                <div className={`num text-[16px] font-semibold ${(s.adaptive.historical.total_r ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>
                  {s.adaptive.historical.total_r}R
                </div>
                <div className="mt-0.5 text-[9px] uppercase tracking-[0.12em] text-txt-faint">total</div>
              </div>
            </div>
          ) : (
            <p className="mt-2 text-[11px] leading-relaxed text-txt-faint">{s.adaptive.historical.note}</p>
          )}

          {/* Evidence breakdown */}
          <Divider className="my-4" />
          <div className="eyebrow mb-3">Evidence breakdown</div>
          <div className="space-y-3">
            {Object.entries(s.adaptive.components).map(([k, v]) => (
              <div key={k}>
                <div className="mb-1 flex justify-between text-[10.5px]">
                  <span className="capitalize text-txt-low">{k.replace(/_/g, " ")}</span>
                  <span className={`num font-semibold ${v >= 60 ? "text-pos" : v >= 35 ? "text-warn" : "text-neg"}`}>
                    {v >= 67 ? "Strong" : v >= 34 ? "Fair" : "Weak"}
                  </span>
                </div>
                <ProgressBar pct={v} />
              </div>
            ))}
          </div>
          {s.adaptive.missing_data.length > 0 && (
            <p className="mt-4 text-[10.5px] leading-relaxed text-txt-faint">
              Missing data (excluded honestly, never guessed): {s.adaptive.missing_data.join(", ").replace(/_/g, " ")}.
              Weights renormalized over available evidence.
            </p>
          )}
        </Glass>
      ) : (
        <p className="mt-4 px-1 text-[10.5px] text-txt-faint">
          Adaptive Quality is not available for this signal - it was created before the intelligence layer existed. New signals are evaluated automatically.
        </p>
      )}

      <div className="mt-5 flex flex-wrap items-center justify-center gap-2 pb-2">
        <Pill tone="neutral">{s.signal_id}</Pill>
        <Pill tone="neutral">{s.market_conditions?.session} session</Pill>
        {s.market_conditions?.volatility_regime && <Pill tone="neutral">{s.market_conditions.volatility_regime} volatility</Pill>}
      </div>
    </div>
  );
}

function LadderRow({ label, value, tone, note, muted, hit }: { label: string; value: string; tone: string; note?: string; muted?: boolean; hit?: boolean }) {
  return (
    <div className={`flex items-baseline justify-between py-[13px] ${muted ? "opacity-35" : ""}`}>
      <span className="flex items-center gap-2 text-[12.5px] text-[var(--text-secondary)]">
        {hit && <span className="text-[var(--accent-green)]">[ok]</span>}
        {label}
      </span>
      <span className="flex items-baseline gap-3">
        {note && <span className="num text-[10.5px] text-[var(--text-muted)]">{note}</span>}
        <span className={`num text-[15px] font-semibold ${tone}`}>{value}</span>
      </span>
    </div>
  );
}
