import { useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, ResponsiveContainer, Tooltip } from "recharts";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Signal } from "../lib/types";
import { ConnectionState, DemoTag, Divider, Eyebrow, Glass, Pill, Segmented, Spinner } from "../components/ui";
import { fmtDateTime, fmtR } from "../lib/format";
import { useNavigate } from "react-router-dom";
import {NotebookPen, ArrowUpRight, ArrowDownRight} from "lucide-react";

export function JournalScreen() {
  const [tab, setTab] = useState<"record" | "history">("record");
  const stats = usePolling<any>(() => api.get(endpoints.journal), 8000);
  const signals = usePolling<{ signals: Signal[] }>(() => api.get(`${endpoints.signals}?status=closed&limit=30`), 8000);
  const navigate = useNavigate();

  if (!stats.data) {
    if (stats.loading) return <Spinner label="Opening the journal..." />;
    return <ConnectionState onRetry={stats.refresh} label="Can't load the journal" />;
  }
  const j = stats.data;
  const m = j?.metrics ?? {};

  const weekEntries = Object.entries(j?.weekly ?? {});
  const weekR = weekEntries.length ? (weekEntries[weekEntries.length - 1][1] as number) : 0;
  const daily = Object.entries(j?.daily ?? {}).map(([day, r]) => ({ day, r: r as number }));

  return (
    <div className="animate-fadeUp">
      <header className="mb-7 flex items-start justify-between">
        <div className="flex items-center gap-3.5">
          <span className="icon-chip icon-chip-cyan" aria-hidden="true"><NotebookPen size={20} /></span>
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight">Journal</h1>
          <p className="mt-1 text-[12.5px] text-txt-low">The complete research record.</p>
          </div>
        </div>
        <DemoTag />
      </header>

      <Segmented
        className="mb-7"
        value={tab}
        onChange={(k) => setTab(k as any)}
        options={[
          { key: "record", label: "Overview" },
          { key: "history", label: "History" },
        ]}
      />

      {tab === "record" && (
        <div className="lg:grid lg:grid-cols-12 lg:gap-10">
          <div className="lg:col-span-7">
            {/* hero numbers */}
            <section>
              <p className="eyebrow">This week</p>
              <div className="mt-2 flex items-baseline gap-3">
                <span
                  className={`num text-[42px] font-extrabold leading-none tracking-[-0.03em] lg:text-[48px] ${weekR >= 0 ? "text-pos" : "text-neg"}`}
                  style={{ textShadow: weekR >= 0 ? "0 0 34px rgba(47,217,138,0.45)" : "0 0 34px rgba(251,77,106,0.4)" }}
                >
                  {fmtR(weekR)}
                </span>
              </div>
              <p className="mt-3 text-[12px] text-txt-low">
                <span className="num font-bold text-txt-hi">{j?.total_signals ?? 0}</span> signals -{" "}
                <span className="num font-bold text-txt-hi">{m.win_rate ?? 0}%</span> win rate -{" "}
                <span className="num font-bold text-txt-hi">{fmtR(m.avg_r)}</span> avg -{" "}
                <span className="num font-bold text-txt-hi">{m.profit_factor ?? "-"}</span> profit factor
              </p>
            </section>

            {/* equity curve */}
            <div className="mt-8">
              <EquityChart curve={(j?.metrics && j?.daily && buildCurve(daily)) ?? []} />
            </div>

            {/* strategy performance - quiet rows */}
            <Eyebrow className="mb-1 mt-9">By strategy</Eyebrow>
            <Glass pad={false} className="mt-2 divide-y divide-white/[0.05] !p-0">
              {Object.entries(j?.by_strategy ?? {}).map(([name, v]: [string, any]) => (
                <div key={name} className="flex items-center justify-between px-5 py-4">
                  <span className="text-[12.5px] text-txt-mid">{name}</span>
                  <span className="num text-[12px] text-txt-low">
                    {v.signals} signals - <span className="font-bold text-txt-hi">{v.win_rate}%</span> - <span className="font-bold text-txt-hi">{fmtR(v.avg_r)}</span>
                  </span>
                </div>
              ))}
            </Glass>
          </div>

          <div className="mt-10 lg:col-span-5 lg:mt-0 lg:border-l lg:border-white/[0.05] lg:pl-10">
            <Eyebrow className="mb-3">Daily R - last 14 days</Eyebrow>
            <Glass pad={false} className="!p-5">
              <div className="flex h-24 items-end gap-[5px]">
                {daily.slice(-14).map((d: any) => (
                  <div key={d.day} className="group relative flex-1">
                    <div
                      className={`w-full rounded-md transition-all duration-500 ${d.r >= 0 ? "bg-pos/60 group-hover:bg-pos" : "bg-neg/60 group-hover:bg-neg"}`}
                      style={{ height: `${Math.min(72, Math.abs(d.r) * 14 + 5)}px` }}
                    />
                    <div className="pointer-events-none absolute -top-7 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-lg border border-white/10 bg-base-2 px-2 py-1 text-[9px] text-txt-mid opacity-0 transition group-hover:opacity-100">
                      {fmtR(d.r)}
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex justify-between text-[9px] text-txt-faint">
                <span>{daily.length > 14 ? daily[daily.length - 14].day.slice(5) : daily[0]?.day?.slice(5) ?? ""}</span>
                <span>today</span>
              </div>
            </Glass>

            <div className="mt-6 grid grid-cols-2 gap-3">
              <Glass level={2} pad={false} className="px-5 py-4">
                <div className="eyebrow !text-[9px]">Taken</div>
                <div className="num mt-1 text-[20px] font-light">{j?.signals_taken ?? 0}</div>
                <div className="mt-0.5 text-[9.5px] text-txt-faint">of {j?.total_signals ?? 0} signals</div>
              </Glass>
              <Glass level={2} pad={false} className="px-5 py-4">
                <div className="eyebrow !text-[9px]">Skipped</div>
                <div className="num mt-1 text-[20px] font-light">{j?.signals_skipped ?? 0}</div>
                <div className="mt-0.5 text-[9.5px] text-txt-faint">still tracked for learning</div>
              </Glass>
            </div>

            <Link to="/analytics" className="btn-ghost mt-6 w-full">Full analytics</Link>
          </div>
        </div>
      )}

      {tab === "history" && (
        <div>
          <Eyebrow className="mb-2">Trade log - every closed signal, full story</Eyebrow>
          <div className="space-y-3">
            {tradeLog(signals.data?.signals ?? [], navigate)}
          </div>
        </div>
      )}
    </div>
  );
}

function buildCurve(daily: { day: string; r: number }[]) {
  let cum = 0;
  return daily.map((d, i) => {
    cum += d.r;
    return { i: i + 1, cum: Math.round(cum * 100) / 100 };
  });
}

function EquityChart({ curve }: { curve: { i: number; cum: number }[] }) {
  if (!curve.length) return null;
  return (
    <div className="glass !p-4">
      <div className="flex items-baseline justify-between px-2 pb-1 pt-1">
        <span className="eyebrow">Cumulative R</span>
        <span className="num text-[12px] font-bold text-pos">{fmtR(curve[curve.length - 1].cum)}</span>
      </div>
      <ResponsiveContainer width="100%" height={140}>
        <AreaChart data={curve} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="jrnl" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#5b9bff" stopOpacity={0.32} />
              <stop offset="100%" stopColor="#6C9EFF" stopOpacity={0} />
            </linearGradient>
          </defs>
          <Tooltip
            cursor={{ stroke: "rgba(255,255,255,0.1)" }}
            contentStyle={{ background: "rgba(16,19,26,0.95)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 14, fontSize: 11 }}
            formatter={(v: any) => [fmtR(v), "Cumulative"]}
            labelFormatter={() => ""}
          />
          <Area type="monotone" dataKey="cum" stroke="#6ea2ff" strokeWidth={2} fill="url(#jrnl)" dot={false} activeDot={{ r: 4, fill: "#fff", stroke: "#5b9bff", strokeWidth: 2 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}


/* ---- trade log: closed signals with TP levels, entry/close times, result ---- */
type LogSignal = {
  id: string; market: string; direction: "BUY" | "SELL"; strategy_name: string;
  signal_id: string; timeframe: string; entry: number; sl: number;
  tp1?: number | null; tp2?: number | null; tp3?: number | null; tp_hits?: number;
  completed?: boolean;
  status?: string; outcome?: string | null; r_multiple?: number;
  candle_time?: string; completed_at?: string; exit_price?: number | null;
  mt5_confirmed?: boolean; mt5_pl?: number;
  market_conditions?: { session?: string };
};

function fmtTime(t?: string) {
  if (!t) return "-";
  const d = new Date(t);
  return isNaN(d.getTime()) ? "-" : d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
function fmtPx(v?: number | null) {
  if (v === null || v === undefined) return "-";
  return v >= 500 ? v.toLocaleString("en-US", { maximumFractionDigits: 1 }) : v.toFixed(v >= 10 ? 3 : 5);
}
function duration(entry?: string, close?: string) {
  if (!entry || !close) return "";
  const ms = new Date(close).getTime() - new Date(entry).getTime();
  if (isNaN(ms) || ms < 0) return "";
  const m = Math.round(ms / 60000);
  return m < 60 ? `${m}m` : m < 1440 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${Math.floor(m / 1440)}d ${Math.floor((m % 1440) / 60)}h`;
}

function tradeLog(signals: LogSignal[], navigate: (p: string) => void) {
  const closed = signals.filter((s) => s.completed).sort((a, b) =>
    String(b.completed_at || b.candle_time).localeCompare(String(a.completed_at || a.candle_time)));
  if (!closed.length) {
    return (
      <Glass className="px-5 py-8 text-center">
        <p className="text-[12.5px] text-txt-mid">No closed trades yet.</p>
        <p className="mt-1 text-[10.5px] text-txt-faint">When a signal hits its TP or SL, the full record lands here.</p>
      </Glass>
    );
  }
  return closed.map((s) => {
    const win = s.outcome === "WIN";
    const tpCount = s.tp_hits ?? 0;
    const resultWord = s.status === "SL_HIT" ? (tpCount > 0 ? `SL after TP${tpCount}` : "SL") : `TP${Math.max(1, tpCount)}`;
    const r = s.r_multiple ?? 0;
    const rColor = r > 0 ? "text-pos" : r < 0 ? "text-neg" : "text-txt-low";
    const dirColor = s.direction === "BUY" ? "text-pos" : "text-neg";
    const tps = [s.tp1, s.tp2, s.tp3].filter((v): v is number => typeof v === "number");
    return (
      <button key={s.id} onClick={() => navigate(`/signals/${s.id}`)}
        className="glass glass-hover tap block w-full p-4 text-left" aria-label={`${s.market} ${s.direction} trade record`}>
        {/* row 1: pair + result */}
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[13px] border text-[13px] font-bold"
            style={{
              borderColor: win ? "rgba(47,217,138,0.45)" : "rgba(251,77,106,0.45)",
              background: win ? "rgba(47,217,138,0.1)" : "rgba(251,77,106,0.1)",
              color: win ? "var(--accent-green)" : "var(--accent-red)",
              boxShadow: win ? "0 0 16px rgba(47,217,138,0.25)" : "0 0 16px rgba(251,77,106,0.25)",
            }} aria-hidden="true">
            {s.direction === "BUY" ? <ArrowUpRight size={16} strokeWidth={2.4} /> : <ArrowDownRight size={16} strokeWidth={2.4} />}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2">
              <span className="text-[15px] font-bold tracking-tight">{s.market}</span>
              <span className={`text-[10.5px] font-bold tracking-wider ${dirColor}`}>{s.direction}</span>
              <span className="text-[9.5px] text-txt-faint">{s.timeframe}</span>
            </div>
            <div className="truncate text-[10px] text-txt-faint">{s.strategy_name} | {s.signal_id}</div>
          </div>
          <div className="shrink-0 text-right">
            <div className={`num text-[17px] font-bold ${rColor}`}>{r > 0 ? "+" : ""}{r.toFixed(1)}R</div>
            <div className={`text-[8.5px] font-bold uppercase tracking-wider ${win ? "text-pos" : "text-neg"}`}>{win ? "WIN" : "LOSS"} - hit {resultWord}</div>
          </div>
        </div>

        {/* row 2: TP ladder */}
        <div className="mt-3 flex gap-1.5">
          {tps.map((tp, i) => (
            <div key={i} className={`num flex-1 rounded-lg border px-2 py-1.5 text-center text-[9.5px] font-semibold ${
              i < tpCount ? "border-[rgba(47,217,138,0.4)] bg-[rgba(47,217,138,0.08)] text-pos" : "border-white/[0.07] bg-white/[0.02] text-txt-faint"}`}>
              TP{i + 1} {i < tpCount ? "hit" : "miss"}
            </div>
          ))}
          <div className={`num flex-1 rounded-lg border px-2 py-1.5 text-center text-[9.5px] font-semibold ${
            s.status === "SL_HIT" ? "border-[rgba(251,77,106,0.4)] bg-[rgba(251,77,106,0.08)] text-neg" : "border-white/[0.07] bg-white/[0.02] text-txt-faint"}`}>
            SL {s.status === "SL_HIT" ? "hit" : "safe"}
          </div>
        </div>

        {/* row 3: prices + times */}
        <div className="mt-3 grid grid-cols-4 gap-2 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
          <div><div className="text-[8px] uppercase tracking-wide text-txt-faint">Entry</div><div className="num text-[11px] font-semibold text-txt-hi">{fmtPx(s.entry)}</div></div>
          <div><div className="text-[8px] uppercase tracking-wide text-txt-faint">Close</div><div className="num text-[11px] font-semibold text-txt-hi">{fmtPx(s.exit_price)}</div></div>
          <div><div className="text-[8px] uppercase tracking-wide text-txt-faint">Opened</div><div className="num text-[10px] font-medium text-txt-mid">{fmtTime(s.candle_time)}</div></div>
          <div><div className="text-[8px] uppercase tracking-wide text-txt-faint">Closed</div><div className="num text-[10px] font-medium text-txt-mid">{fmtTime(s.completed_at)}</div></div>
        </div>
        <div className="mt-2 flex items-center justify-between text-[9px] text-txt-faint">
          <span>Held {duration(s.candle_time, s.completed_at)}{(s.market_conditions as any)?.session ? ` - ${(s.market_conditions as any).session} session` : ""}</span>
          {s.mt5_confirmed ? <span className="font-semibold text-pos">MT5 confirmed</span> : <span>tap for full analysis</span>}
        </div>
      </button>
    );
  });
}
