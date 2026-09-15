import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ChevronDown, CircleSlash, NotebookPen } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Candle, Signal } from "../lib/types";
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
  const [error, setError] = useState("");

  const { data } = usePolling<{ signal: Signal }>(() => api.get(endpoints.signal(id!)), 5000);
  const s = data?.signal;
  const candlesQ = usePolling<{ candles: Candle[] }>(
    async () => (s ? api.get(`/api/markets/${s.market}/candles?tf=${s.timeframe}&limit=140`) : { candles: [] }),
    15000,
    [s?.market, s?.timeframe]
  );

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
        <button onClick={() => navigate(-1)} className="tap -ml-2 flex items-center gap-1.5 rounded-full p-2 text-[13px] font-semibold text-[var(--text-secondary)] transition hover:text-white">
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
                borderColor: buy ? "rgba(47,217,138,0.4)" : "rgba(251,77,106,0.4)",
                color: buy ? "var(--accent-green)" : "var(--accent-red)",
                background: buy ? "rgba(47,217,138,0.1)" : "rgba(251,77,106,0.1)",
                boxShadow: buy ? "0 0 22px rgba(47,217,138,0.2)" : "0 0 22px rgba(251,77,106,0.2)",
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
          <LadderRow label="Entry" value={fmtPrice(s.entry)} tone="text-[#8fb4ff]" note={s.entry_zone && s.entry_zone[0] !== s.entry_zone[1] ? `zone ${fmtPrice(s.entry_zone[0])}-${fmtPrice(s.entry_zone[1])}` : undefined} />
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
              className={`rounded-full border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-[10px] font-semibold ${
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
              <NotebookPen size={14} className="text-[#b3a6ff]" /> Result analysis
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
