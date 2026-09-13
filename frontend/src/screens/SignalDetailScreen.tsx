import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ChevronDown, CircleSlash, NotebookPen } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Candle, Signal } from "../lib/types";
import { DemoTag, Divider, Glass, GlowDot, Pill, Spinner, ThinProgress } from "../components/ui";
import { CandleChart, ChartLine } from "../components/CandleChart";
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
  const [error, setError] = useState("");

  const { data } = usePolling<{ signal: Signal }>(() => api.get(endpoints.signal(id!)), 5000);
  const s = data?.signal;
  const candlesQ = usePolling<{ candles: Candle[] }>(
    async () => (s ? api.get(`/api/markets/${s.market}/candles?tf=${s.timeframe}&limit=140`) : { candles: [] }),
    15000,
    [s?.market, s?.timeframe]
  );

  if (!s) return <Spinner label="Loading signal…" />;
  const buy = s.direction === "BUY";

  const lines: ChartLine[] = [
    { price: s.sl, color: "#F0788C", label: "SL", style: "dashed", fade: 0.7 },
    { price: s.entry, color: "#6C9EFF", label: "Entry", style: "solid", fade: 0.9 },
    ...(s.tp1 ? [{ price: s.tp1, color: "#3ECF8E", label: "TP1", style: "dashed" as const, fade: 0.8 }] : []),
    ...(s.tp2 ? [{ price: s.tp2, color: "#3ECF8E", label: "TP2", style: "dashed" as const, fade: 0.55 }] : []),
    ...(s.tp3 ? [{ price: s.tp3, color: "#3ECF8E", label: "TP3", style: "dashed" as const, fade: 0.35 }] : []),
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

  return (
    <div className="animate-fadeUp">
      <header className="mb-5 flex items-center justify-between">
        <button onClick={() => navigate(-1)} className="tap -ml-2 flex items-center gap-1.5 rounded-full p-2 text-[13px] font-medium text-txt-mid transition hover:text-txt-hi">
          <ArrowLeft size={17} /> Signals
        </button>
        <DemoTag />
      </header>

      {/* ---- title ---- */}
      <div className="flex items-start justify-between px-1">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-[28px] font-semibold tracking-tight">{s.market}</h1>
            <span
              className="flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-bold tracking-[0.1em]"
              style={{
                borderColor: buy ? "rgba(62,207,142,0.35)" : "rgba(240,120,140,0.35)",
                color: buy ? "#3ECF8E" : "#F0788C",
                background: buy ? "rgba(62,207,142,0.08)" : "rgba(240,120,140,0.08)",
                boxShadow: buy ? "0 0 20px rgba(62,207,142,0.15)" : "0 0 20px rgba(240,120,140,0.15)",
              }}
            >
              <GlowDot tone={buy ? "pos" : "neg"} size={6} pulse={false} />
              {s.direction}
            </span>
          </div>
          <p className="mt-1.5 text-[12px] text-txt-low">
            {s.strategy_name} <span className="text-txt-faint">· v{s.strategy_version} · {s.timeframe} · {fmtDateTime(s.candle_time)}</span>
          </p>
        </div>
        <div className="text-right">
          <div className="eyebrow">Score</div>
          <div className="num text-[22px] font-medium text-txt-hi">{s.score}</div>
        </div>
      </div>

      {/* ---- chart centerpiece ---- */}
      <Glass className="mt-6 !px-2 !py-3">
        <CandleChart
          candles={candlesQ.data?.candles ?? []}
          lines={lines}
          entry={s.entry}
          sl={s.sl}
          tp1={s.tp1 ?? undefined}
          markerTime={s.candle_time}
          markerDirection={s.direction}
          height={s.tp3 ? 340 : 300}
        />
      </Glass>

      {/* ---- levels ladder ---- */}
      <Glass className="mt-4 !py-2" pad={false}>
        <div className="px-5">
          <LadderRow label="Entry" value={fmtPrice(s.entry)} tone="text-acc" note={s.entry_zone && s.entry_zone[0] !== s.entry_zone[1] ? `zone ${fmtPrice(s.entry_zone[0])}–${fmtPrice(s.entry_zone[1])}` : undefined} />
          <Divider />
          <LadderRow label="Stop loss" value={fmtPrice(s.sl)} tone="text-neg" note={`risk ${fmtPrice(s.risk)}`} />
          <Divider />
          <LadderRow label="TP1" value={fmtPrice(s.tp1)} tone="text-pos" note="1R" muted={!!s.completed && (s.tp_hits ?? 0) < 1} hit={(s.tp_hits ?? 0) >= 1} />
          <Divider />
          {s.tp2 && <LadderRow label="TP2" value={fmtPrice(s.tp2)} tone="text-pos" note="2R" muted={!!s.completed && (s.tp_hits ?? 0) < 2} hit={(s.tp_hits ?? 0) >= 2} />}
          {s.tp2 && <Divider />}
          {s.tp3 && <LadderRow label="TP3" value={fmtPrice(s.tp3)} tone="text-pos" note="3R" muted={!!s.completed && (s.tp_hits ?? 0) < 3} hit={(s.tp_hits ?? 0) >= 3} />}
          {s.tp3 && <Divider />}
          <LadderRow label="Risk / reward" value={`1 : ${s.rr_primary}`} tone="text-txt-hi" note={s.completed ? fmtR(s.r_multiple) : s.status.replace("_", " ")} />
        </div>
      </Glass>

      {/* ---- MTF ---- */}
      {s.mtf && (
        <div className="mt-4 flex items-center gap-2 px-2">
          <span className="eyebrow mr-1">MTF</span>
          {Object.entries(s.mtf).map(([tf, v]) => (
            <span key={tf} className={`rounded-full border border-white/[0.07] bg-white/[0.03] px-2.5 py-1 text-[10px] font-medium ${
              v === 1 ? "text-pos" : v === -1 ? "text-neg" : "text-txt-low"
            }`}>
              {tf} <span className="font-semibold">{TREND_LABEL[v]?.slice(0, 4) ?? "–"}</span>
            </span>
          ))}
        </div>
      )}

      {/* ---- why it qualifies (expandable) ---- */}
      <div className="mt-4">
        <button onClick={() => setWhyOpen(!whyOpen)} className="tap glass-2 w-full px-5 py-4 text-left">
          <div className="flex items-center justify-between">
            <span className="text-[13px] font-medium text-txt-hi">Why it qualifies</span>
            <ChevronDown size={16} className={`text-txt-low transition-transform duration-500 ${whyOpen ? "rotate-180" : ""}`} />
          </div>
          {!whyOpen && firstCheck && (
            <p className="mt-1.5 truncate text-[12px] text-pos">✓ {firstCheck.label}</p>
          )}
        </button>
        <div className={`grid transition-all duration-500 ease-out ${whyOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
          <div className="overflow-hidden">
            <div className="glass-2 mt-2 space-y-3.5 px-5 py-5">
              {(s.checks ?? []).map((c, i) => (
                <div key={i}>
                  <div className={`flex items-start gap-2.5 text-[12.5px] ${c.ok ? "text-txt-mid" : "text-txt-faint"}`}>
                    <span className={c.ok ? "text-pos" : "text-txt-faint"}>✓</span>
                    <span className="font-medium text-txt-hi/90">{c.label}</span>
                  </div>
                  <div className="ml-[22px] mt-0.5 text-[11px] text-txt-faint">{c.detail}</div>
                </div>
              ))}
              <Divider />
              <div>
                <div className="eyebrow mb-2">AI analysis</div>
                <p className="text-[12.5px] leading-relaxed text-txt-mid">{s.reason}</p>
              </div>
              {/* score composition */}
              <div>
                <div className="eyebrow mb-2.5">Score composition</div>
                <div className="space-y-2.5">
                  {Object.entries(s.score_components || {}).map(([k, v]) => (
                    <div key={k}>
                      <div className="mb-1 flex justify-between text-[10.5px] text-txt-low">
                        <span>{k}</span>
                        <span className="num text-txt-mid">+{v}</span>
                      </div>
                      <ThinProgress pct={(v / 40) * 100} />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ---- your decision ---- */}
      <Glass className="mt-4">
        <div className="eyebrow mb-3">Your decision</div>
        {!s.user_action && !mode && (
          <>
            <p className="mb-4 text-[12.5px] text-txt-low">Did you enter this trade on MT5?</p>
            <div className="flex gap-2.5">
              <button className="btn-primary flex-1" onClick={() => setMode("entered")}>Yes, I entered</button>
              <button className="btn-ghost flex-1" onClick={() => setMode("skipped")}>I skipped</button>
            </div>
          </>
        )}
        {mode === "entered" && (
          <div className="space-y-2.5 animate-fadeUp">
            <input className="input" placeholder={`Entry price — default ${fmtPrice(s.entry)}`} inputMode="decimal" value={entryPrice} onChange={(e) => setEntryPrice(e.target.value)} />
            <input className="input" placeholder="Notes (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} />
            <div className="flex gap-2.5">
              <button className="btn-primary flex-1" disabled={busy} onClick={() => act("entered")}>Record entry</button>
              <button className="btn-ghost" onClick={() => setMode(null)}>Cancel</button>
            </div>
          </div>
        )}
        {mode === "skipped" && (
          <div className="space-y-2.5 animate-fadeUp">
            <input className="input" placeholder="Why did you skip? (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} />
            <div className="flex gap-2.5">
              <button className="btn-ghost flex-1" disabled={busy} onClick={() => act("skipped")}>
                <CircleSlash size={13} /> Confirm skip
              </button>
              <button className="btn-ghost" onClick={() => setMode(null)}>Cancel</button>
            </div>
          </div>
        )}
        {s.user_action === "entered" && (
          <div className="flex items-center gap-2.5 text-[12.5px] text-acc-cyan">
            <GlowDot tone="acc" size={6} pulse={false} />
            Entered at <span className="num font-semibold">{fmtPrice(s.user_entry_price ?? s.entry)}</span>
            <span className="text-txt-faint">— your result is tracked separately from the signal's.</span>
          </div>
        )}
        {s.user_action === "skipped" && (
          <div className="flex items-center gap-2.5 text-[12.5px] text-txt-low">
            <CircleSlash size={13} /> Skipped — the agent still follows this signal to learn from it.
          </div>
        )}
        {error && <div className="mt-3 text-[11.5px] text-neg">{error}</div>}
      </Glass>

      {/* ---- result analysis ---- */}
      {s.result_analysis && (
        <div className="mt-4">
          <button onClick={() => setAnalysisOpen(!analysisOpen)} className="tap glass-2 flex w-full items-center justify-between px-5 py-4">
            <span className="flex items-center gap-2 text-[13px] font-medium text-txt-hi">
              <NotebookPen size={14} className="text-acc-violet" /> Result analysis
            </span>
            <ChevronDown size={16} className={`text-txt-low transition-transform duration-500 ${analysisOpen ? "rotate-180" : ""}`} />
          </button>
          <div className={`grid transition-all duration-500 ease-out ${analysisOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
            <div className="overflow-hidden">
              <div className="glass-2 mt-2 px-5 py-5">
                <p className="text-[12.5px] leading-relaxed text-txt-mid">{s.result_analysis.what_happened}</p>
                <div className="mt-3 space-y-1">
                  {(s.result_analysis.setup_conditions ?? []).map((c: string, i: number) => (
                    <div key={i} className="text-[11px] text-txt-faint">· {c}</div>
                  ))}
                </div>
                <Divider className="my-3.5" />
                <p className="text-[11px] leading-relaxed text-txt-low">{s.result_analysis.isolated_or_pattern}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="mt-5 flex items-center justify-center gap-2 pb-2">
        <Pill tone="neutral">{s.signal_id}</Pill>
        <Pill tone="neutral">{s.market_conditions?.session} session</Pill>
        {s.market_conditions?.volatility_regime && <Pill tone="neutral">{s.market_conditions.volatility_regime} volatility</Pill>}
      </div>
    </div>
  );
}

function LadderRow({ label, value, tone, note, muted, hit }: { label: string; value: string; tone: string; note?: string; muted?: boolean; hit?: boolean }) {
  return (
    <div className={`flex items-baseline justify-between py-[13px] ${muted ? "opacity-40" : ""}`}>
      <span className="flex items-center gap-2 text-[12px] text-txt-low">
        {hit && <span className="text-pos">●</span>}
        {label}
      </span>
      <span className="flex items-baseline gap-2.5">
        {note && <span className="num text-[10.5px] text-txt-faint">{note}</span>}
        <span className={`num text-[14.5px] font-semibold ${tone}`}>{value}</span>
      </span>
    </div>
  );
}
