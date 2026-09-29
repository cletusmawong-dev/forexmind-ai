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
  const [tab, setTab] = useState<"record" | "journal" | "history">("record");
  const stats = usePolling<any>(() => api.get(endpoints.journal), 8000);
  const signals = usePolling<{ signals: Signal[] }>(() => api.get(`${endpoints.signals}?status=closed&limit=200`), 8000);
  const mt5 = usePolling<any>(() => api.get("/api/journal/mt5-trades"), 8000);
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
          { key: "journal", label: "My journal" },
          { key: "history", label: "Trade history" },
        ]}
      />

      {tab === "record" && (
        <div className="lg:grid lg:grid-cols-12 lg:gap-10">
          <div className="lg:col-span-7" data-reveal>
            {/* hero numbers */}
            <section>
              <p className="eyebrow">This week</p>
              <div className="mt-2 flex items-baseline gap-3">
                <span
                  className={`num text-[42px] font-extrabold leading-none tracking-[-0.03em] lg:text-[48px] ${weekR >= 0 ? "text-pos" : "text-neg"}`}
                  style={{ textShadow: weekR >= 0 ? "0 0 34px rgba(var(--p-rgb),0.45)" : "0 0 34px rgba(var(--neg-rgb),0.4)" }}
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
            <Glass pad={false} className="mt-2 divide-y divide-[rgba(var(--warm-rgb),0.06)] !p-0">
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

          <div className="mt-10 lg:col-span-5 lg:mt-0 lg:border-l lg:border-[rgba(var(--warm-rgb),0.06)] lg:pl-10" data-reveal>
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
              <button onClick={() => setTab("journal")} className="text-left">
                <Glass level={2} pad={false} className="cursor-pointer px-5 py-4 transition hover:bg-[var(--glass-surface-hover)]">
                  <div className="eyebrow !text-[9px]">Taken</div>
                  <div className="num mt-1 text-[20px] font-light">{j?.signals_taken ?? 0}</div>
                  <div className="mt-0.5 text-[9.5px] text-txt-faint">of {j?.total_signals ?? 0} signals - view journal</div>
                </Glass>
              </button>
              <button onClick={() => setTab("journal")} className="text-left">
                <Glass level={2} pad={false} className="cursor-pointer px-5 py-4 transition hover:bg-[var(--glass-surface-hover)]">
                  <div className="eyebrow !text-[9px]">Skipped</div>
                  <div className="num mt-1 text-[20px] font-light">{j?.signals_skipped ?? 0}</div>
                  <div className="mt-0.5 text-[9.5px] text-txt-faint">still tracked for learning - view journal</div>
                </Glass>
              </button>
            </div>

            <Link to="/analytics" className="btn-ghost mt-6 w-full">Full analytics</Link>
          </div>
        </div>
      )}

      {tab === "journal" && <MyJournalTab onOpenHistory={() => setTab("history")} />}

      {tab === "history" && (
        <HistoryTab raw={signals.data?.signals ?? []} mt5Data={mt5.data} navigate={navigate} />
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
              <stop offset="0%" stopColor="var(--c-p1)" stopOpacity={0.32} />
              <stop offset="100%" stopColor="#6C9EFF" stopOpacity={0} />
            </linearGradient>
          </defs>
          <Tooltip
            cursor={{ stroke: "rgba(255,255,255,0.1)" }}
            contentStyle={{ background: "rgba(16,19,26,0.95)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 14, fontSize: 11 }}
            formatter={(v: any) => [fmtR(v), "Cumulative"]}
            labelFormatter={() => ""}
          />
          <Area type="monotone" dataKey="cum" stroke="var(--c-mag)" strokeWidth={2} fill="url(#jrnl)" dot={false} activeDot={{ r: 4, fill: "#fff", stroke: "var(--c-p1)", strokeWidth: 2 }} />
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
  extra_signal?: boolean;
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
              borderColor: win ? "rgba(var(--p-rgb),0.45)" : "rgba(var(--neg-rgb),0.45)",
              background: win ? "rgba(var(--p-rgb),0.1)" : "rgba(var(--neg-rgb),0.1)",
              color: win ? "var(--accent-green)" : "var(--accent-red)",
              boxShadow: win ? "0 0 16px rgba(var(--p-rgb),0.25)" : "0 0 16px rgba(var(--neg-rgb),0.25)",
            }} aria-hidden="true">
            {s.direction === "BUY" ? <ArrowUpRight size={16} strokeWidth={2.4} /> : <ArrowDownRight size={16} strokeWidth={2.4} />}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2">
              <span className="text-[15px] font-bold tracking-tight">{s.market}</span>
              <span className={`text-[10.5px] font-bold tracking-wider ${dirColor}`}>{s.direction}</span>
              <span className="text-[9.5px] text-txt-faint">{s.timeframe}</span>
              {s.extra_signal && (
                <span className="rounded-full border border-[var(--accent-amber)] bg-[rgba(var(--warm-rgb),0.08)] px-1.5 py-0.5 text-[8px] font-bold tracking-[0.06em] text-[var(--accent-amber)]">
                  EXTRA · NOT ENTERED
                </span>
              )}
            </div>
            <div className="truncate text-[10px] text-txt-faint">{s.strategy_name} | {s.signal_id}</div>
          </div>
          <div className="shrink-0 text-right">
            <div className={`num text-[17px] font-bold ${rColor}`}>{r > 0 ? "+" : ""}{r.toFixed(1)}R</div>
            {s.mt5_confirmed && s.mt5_pl != null ? (
              <div className={`num text-[11px] font-bold ${s.mt5_pl >= 0 ? "text-pos" : "text-neg"}`}>
                {s.mt5_pl >= 0 ? "+" : "-"}${Math.abs(s.mt5_pl).toFixed(2)} <span className="text-[8px] font-semibold uppercase tracking-wider text-txt-faint">MT5</span>
              </div>
            ) : null}
            <div className={`text-[8.5px] font-bold uppercase tracking-wider ${win ? "text-pos" : "text-neg"}`}>{win ? "WIN" : "LOSS"} - hit {resultWord}</div>
          </div>
        </div>

        {/* row 2: TP ladder */}
        <div className="mt-3 flex gap-1.5">
          {tps.map((tp, i) => (
            <div key={i} className={`num flex-1 rounded-lg border px-2 py-1.5 text-center text-[9.5px] font-semibold ${
              i < tpCount ? "border-[rgba(var(--p-rgb),0.4)] bg-[rgba(var(--p-rgb),0.08)] text-pos" : "border-[rgba(var(--warm-rgb),0.08)] bg-[rgba(var(--warm-rgb),0.035)] text-txt-faint"}`}>
              TP{i + 1} {i < tpCount ? "hit" : "miss"}
            </div>
          ))}
          <div className={`num flex-1 rounded-lg border px-2 py-1.5 text-center text-[9.5px] font-semibold ${
            s.status === "SL_HIT" ? "border-[rgba(var(--neg-rgb),0.4)] bg-[rgba(var(--neg-rgb),0.08)] text-neg" : "border-[rgba(var(--warm-rgb),0.08)] bg-[rgba(var(--warm-rgb),0.035)] text-txt-faint"}`}>
            SL {s.status === "SL_HIT" ? "hit" : "safe"}
          </div>
        </div>

        {/* row 3: prices + times */}
        <div className="mt-3 grid grid-cols-4 gap-2 rounded-xl border border-[rgba(var(--warm-rgb),0.07)] bg-[rgba(var(--warm-rgb),0.035)] px-3 py-2.5">
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

/** MT5 real-money trades: LIVE rows with floating $, CLOSED with broker-confirmed $. */
function mt5Trades(trades: any[], navigate: (p: string) => void, mode: string) {
  if (mode !== "vps" && mode !== "manual") return null;
  if (!trades.length) {
    return (
      <Glass className="px-5 py-6 text-center">
        <p className="text-[12.5px] text-txt-mid">No MT5-executed trades yet.</p>
        <p className="mt-1 text-[10.5px] text-txt-faint">Every auto-executed trade lands here with its dollar result - live while open, broker-confirmed when closed.</p>
      </Glass>
    );
  }
  return (
    <>
      <Eyebrow className="mb-2">MT5 trades - real executions, dollar results</Eyebrow>
      <div className="space-y-2.5">
        {trades.map((t) => {
          const live = t.state === "LIVE";
          const win = (t.pl_usd ?? 0) > 0;
          const neutral = t.pl_usd == null;
          return (
            <button key={t.id || t.ticket} onClick={() => t.id && navigate(`/signals/${t.id}`)}
              className="glass glass-hover tap block w-full p-4 text-left" aria-label={`MT5 trade ${t.market}`}>
              <div className="flex items-center gap-3">
                <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-[13px] border ${
                  live ? "border-[rgba(var(--p-rgb),0.45)] bg-[rgba(var(--p-rgb),0.1)] text-pos"
                    : neutral ? "border-[rgba(var(--warm-rgb),0.15)] bg-[rgba(var(--warm-rgb),0.05)] text-txt-mid"
                    : win ? "border-[rgba(var(--p-rgb),0.45)] bg-[rgba(var(--p-rgb),0.1)] text-pos"
                          : "border-[rgba(var(--neg-rgb),0.45)] bg-[rgba(var(--neg-rgb),0.1)] text-neg"
                }`} aria-hidden="true">
                  {t.direction === "BUY" ? <ArrowUpRight size={16} strokeWidth={2.4} /> : <ArrowDownRight size={16} strokeWidth={2.4} />}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <span className="text-[15px] font-bold tracking-tight">{t.market}</span>
                    <span className={`text-[10.5px] font-bold tracking-wider ${t.direction === "BUY" ? "text-pos" : "text-neg"}`}>{t.direction}</span>
                    <span className="num text-[9.5px] text-txt-faint">{t.volume ?? "-"} lots</span>
                    {live && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-[rgba(var(--p-rgb),0.35)] bg-[rgba(var(--p-rgb),0.08)] px-1.5 py-0.5 text-[8px] font-bold tracking-wider text-pos">
                        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent-green)]" /> LIVE
                      </span>
                    )}
                  </div>
                  <div className="truncate text-[10px] text-txt-faint">
                    #{t.ticket} - {t.signal_id}{t.state === "SUBMITTED" ? " - submitted" : ""}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className={`num text-[17px] font-bold ${neutral ? "text-txt-low" : win ? "text-pos" : "text-neg"}`}>
                    {neutral ? "-" : `${(t.pl_usd ?? 0) > 0 ? "+" : ""}$${(t.pl_usd ?? 0).toFixed(2)}`}
                  </div>
                  <div className={`text-[8.5px] font-bold uppercase tracking-wider ${neutral ? "text-txt-faint" : win ? "text-pos" : "text-neg"}`}>
                    {live ? "floating now"
                      : t.state === "SUBMITTED" ? "pending fill"
                      : neutral ? "syncing broker..."
                      : `${(t.outcome || (win ? "WIN" : "LOSS"))} - broker confirmed`}
                  </div>
                </div>
              </div>
              <div className="mt-2.5 grid grid-cols-4 gap-2 rounded-xl border border-[rgba(var(--warm-rgb),0.07)] bg-[rgba(var(--warm-rgb),0.035)] px-3 py-2 text-center">
                <div><div className="text-[8px] uppercase tracking-wide text-txt-faint">Open</div><div className="num text-[10.5px] font-semibold">{t.open_price ? Number(t.open_price).toFixed(t.open_price >= 10 ? 3 : 5) : "-"}</div></div>
                <div><div className="text-[8px] uppercase tracking-wide text-txt-faint">SL</div><div className="num text-[10.5px] font-semibold">{t.sl ? Number(t.sl).toFixed(t.sl >= 10 ? 3 : 5) : "-"}</div></div>
                <div><div className="text-[8px] uppercase tracking-wide text-txt-faint">TP set</div><div className="num text-[10.5px] font-semibold">{(t.tp2 || t.tp1) ? Number(t.tp2 || t.tp1).toFixed((t.tp2 || t.tp1) >= 10 ? 3 : 5) : "-"}</div></div>
                <div><div className="text-[8px] uppercase tracking-wide text-txt-faint">{live ? "Opened" : "Closed"}</div><div className="num text-[10.5px] font-medium">{fmtTime(live ? t.opened_at : (t.closed_at || t.opened_at))}</div></div>
              </div>
            </button>
          );
        })}
      </div>
    </>
  );
}

/* ---- trade history: win-rate stats + breakdowns + filters + full log ---- */
function StatTile({ label, value, tone, sub }: { label: string; value: string; tone?: string; sub?: string }) {
  return (
    <div className="glass px-4 py-3.5">
      <div className="eyebrow !text-[9px]">{label}</div>
      <div className={`num mt-1 text-[19px] font-bold ${tone ?? "text-txt-hi"}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[9.5px] text-txt-faint">{sub}</div>}
    </div>
  );
}

function BreakdownTable({ title, rows }: { title: string; rows: { name: string; c: number; wr: number; r: number }[] }) {
  if (!rows.length) return null;
  return (
    <div className="glass px-5 py-4">
      <div className="eyebrow !text-[9px]">{title}</div>
      <div className="mt-2 space-y-2">
        {rows.map((r) => (
          <div key={r.name} className="flex items-center gap-3">
            <span className="w-24 shrink-0 truncate text-[11.5px] text-txt-mid">{r.name}</span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[rgba(var(--warm-rgb),0.1)]">
              <div className={`h-full rounded-full ${r.wr >= 50 ? "bg-pos" : "bg-[var(--accent-amber)]"}`}
                style={{ width: `${Math.max(4, r.wr)}%`, transition: "width .6s ease" }} />
            </div>
            <span className={`num w-10 text-right text-[11.5px] font-bold ${r.wr >= 50 ? "text-pos" : "text-[var(--accent-amber)]"}`}>{r.wr}%</span>
            <span className="num w-16 text-right text-[10px] text-txt-faint">{r.c} trd - {fmtR(r.r)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function HistoryTab({ raw, mt5Data, navigate }: { raw: LogSignal[]; mt5Data: any; navigate: (p: string) => void }) {
  const [outcome, setOutcome] = useState<"all" | "win" | "loss">("all");
  const [strat, setStrat] = useState("all");

  const closed = raw.filter((s) => s.completed).sort((a, b) =>
    String(b.completed_at || b.candle_time).localeCompare(String(a.completed_at || a.candle_time)));
  const wins = closed.filter((s) => s.outcome === "WIN").length;
  const losses = closed.length - wins;
  const wr = closed.length ? Math.round((100 * wins) / closed.length) : 0;
  const rs = closed.map((s) => s.r_multiple ?? 0);
  const totalR = rs.reduce((a, b) => a + b, 0);
  const avgR = closed.length ? totalR / closed.length : 0;
  const best = rs.length ? Math.max(...rs) : 0;
  const worst = rs.length ? Math.min(...rs) : 0;
  const posR = rs.filter((r) => r > 0).reduce((a, b) => a + b, 0);
  const negR = Math.abs(rs.filter((r) => r < 0).reduce((a, b) => a + b, 0));
  const pf = negR > 0 ? posR / negR : posR > 0 ? Infinity : 0;

  const mt5Closed = (mt5Data?.trades ?? []).filter((t: any) => t.closed_at || (t.status ?? "") === "closed");
  const mt5Sum = mt5Closed.reduce((a: number, t: any) => a + (t.pl_usd ?? 0), 0);

  const breakdown = (key: "strategy_name" | "market") => {
    const groups = [...new Set(closed.map((s) => s[key]).filter((g): g is string => !!g))];
    return groups.map((g) => {
      const arr = closed.filter((s) => s[key] === g);
      const w = arr.filter((s) => s.outcome === "WIN").length;
      return {
        name: g, c: arr.length,
        wr: arr.length ? Math.round((100 * w) / arr.length) : 0,
        r: arr.reduce((a, s) => a + (s.r_multiple ?? 0), 0),
      };
    }).sort((a, b) => b.wr - a.wr || b.c - a.c);
  };
  const byStrat = breakdown("strategy_name");
  const byPair = breakdown("market");
  const stratNames = byStrat.map((b) => b.name);

  const filtered = closed.filter((s) =>
    (outcome === "all" || (outcome === "win" ? s.outcome === "WIN" : s.outcome !== "WIN")) &&
    (strat === "all" || s.strategy_name === strat));

  const chip = (on: boolean) =>
    `rounded-full px-3 py-1.5 text-[10.5px] font-semibold transition ${on ? "chip-on-pos" : "chip-off"}`;

  return (
    <div>
      {/* win-rate stat tiles */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4" data-reveal>
        <div className="glass relative overflow-hidden px-5 py-4">
          <div className="eyebrow !text-[9px]">Win rate</div>
          <div className={`num mt-1 text-[34px] font-extrabold leading-none ${wr >= 50 ? "text-pos" : wr > 0 ? "text-[var(--accent-amber)]" : "text-neg"}`}>{wr}%</div>
          <div className="mt-1 text-[9.5px] text-txt-faint">{wins}W - {losses}L of {closed.length} closed</div>
          <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-[rgba(var(--warm-rgb),0.1)]">
            <div className={`h-full rounded-full ${wr >= 50 ? "bg-pos" : "bg-[var(--accent-amber)]"}`}
              style={{ width: `${wr}%`, transition: "width .6s ease" }} />
          </div>
        </div>
        <StatTile label="Closed trades" value={String(closed.length)} sub={`${wins} wins - ${losses} losses`} />
        <StatTile label="Total R" value={fmtR(totalR)} tone={totalR >= 0 ? "text-pos" : "text-neg"}
          sub={`best ${fmtR(best)} - worst ${fmtR(worst)}`} />
        <StatTile label="Avg R / trade" value={fmtR(avgR)} tone={avgR >= 0 ? "text-pos" : "text-neg"} sub="across all closed" />
        <StatTile label="Profit factor" value={pf === Infinity ? "\u221e" : pf.toFixed(2)}
          tone={pf >= 1 ? "text-pos" : "text-neg"} sub="gross win R \u00f7 gross loss R" />
        <StatTile label="MT5 realized" value={`${mt5Sum >= 0 ? "+" : ""}$${Math.abs(mt5Sum).toFixed(2)}`}
          tone={mt5Sum >= 0 ? "text-pos" : "text-neg"} sub={`${mt5Closed.length} broker-confirmed`} />
        <StatTile label="Taken / skipped" value={`${wins + losses}`} sub="closed vs tracked signals" />
      </div>

      {/* win rate by strategy / by pair */}
      <div className="mt-5 grid gap-3 lg:grid-cols-2" data-reveal>
        <BreakdownTable title="Win rate by strategy" rows={byStrat} />
        <BreakdownTable title="Win rate by pair" rows={byPair} />
      </div>

      {/* filters */}
      <div className="mt-7 flex flex-wrap items-center gap-2" data-reveal>
        <span className="eyebrow !mb-0 !text-[9px]">Filter</span>
        {(["all", "win", "loss"] as const).map((k) => (
          <button key={k} onClick={() => setOutcome(k)} className={chip(outcome === k)}>
            {k === "all" ? `All (${closed.length})` : k === "win" ? `Wins ${wins}` : `Losses ${losses}`}
          </button>
        ))}
        <span className="mx-1 h-4 w-px bg-[rgba(var(--warm-rgb),0.12)]" />
        <button onClick={() => setStrat("all")} className={chip(strat === "all")}>All strategies</button>
        {stratNames.map((n) => (
          <button key={n} onClick={() => setStrat(n)} className={chip(strat === n)}>{n}</button>
        ))}
      </div>

      {/* logs */}
      <div className="mt-5" data-reveal>
        {mt5Trades(mt5Data?.trades ?? [], navigate, mt5Data?.mode)}
        <Eyebrow className="mt-8 mb-2">
          Trade log - every closed signal, full story{filtered.length !== closed.length ? ` (${filtered.length} of ${closed.length} shown)` : ""}
        </Eyebrow>
        <div className="space-y-3">
          {tradeLog(filtered, navigate)}
        </div>
      </div>
    </div>
  );
}

/* ---- my journal: the user's OWN journaling record (notes + personal result) ---- */
function MyJournalTab({ onOpenHistory }: { onOpenHistory: () => void }) {
  const q = usePolling<{ entries: any[]; count: number; taken: number; skipped: number;
    notes_written: number }>(() => api.get(endpoints.journalEntries), 10000);
  if (!q.data) {
    if (q.loading) return <Spinner label="Opening your journal..." />;
    return <ConnectionState onRetry={q.refresh} label="Can't load your journal" />;
  }
  const entries = q.data.entries ?? [];
  return (
    <div className="animate-fadeUp" data-reveal>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Pill tone="cyan">{q.data.taken} taken</Pill>
        <Pill tone="warn">{q.data.skipped} skipped</Pill>
        <Pill tone="pos">{q.data.notes_written} with notes</Pill>
        <span className="text-[10.5px] text-txt-faint">
          Written from a signal - open one and tap "I took this trade".
        </span>
      </div>
      {entries.length === 0 && (
        <Glass className="px-5 py-10 text-center">
          <NotebookPen size={22} className="mx-auto text-txt-faint" />
          <p className="mt-2 text-[13px] font-medium text-txt-hi">No journal entries yet</p>
          <p className="mx-auto mt-1 max-w-sm text-[11.5px] leading-relaxed text-txt-low">
            Open any signal and record it as taken or skipped - with your own note on why.
            Your entries and their outcomes collect here, separate from the engine log
            (<button className="underline" onClick={onOpenHistory}>trade history</button>).
          </p>
        </Glass>
      )}
      <div className="space-y-3">
        {entries.map((e) => {
          const took = e.action === "entered";
          const win = e.outcome === "WIN";
          return (
            <Glass key={e.id} className="px-5 py-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Pill tone={took ? "pos" : "warn"}>{took ? "TOOK" : "SKIPPED"}</Pill>
                  <span className="text-[13px] font-semibold text-txt-hi">
                    {e.market} {e.direction}
                  </span>
                  <span className="text-[10px] text-txt-faint">{e.strategy_name}</span>
                </div>
                {e.outcome ? (
                  <span className={`num text-[12.5px] font-bold ${win ? "text-pos" : "text-neg"}`}>
                    {e.outcome}{e.r_multiple != null ? ` - ${e.r_multiple > 0 ? "+" : ""}${e.r_multiple}R` : ""}
                  </span>
                ) : (
                  <span className="text-[10.5px] text-txt-faint">still tracking</span>
                )}
              </div>
              <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[10.5px] text-txt-faint">
                {took && e.user_entry_price != null && (
                  <span className="num">your entry {e.user_entry_price}</span>
                )}
                {took && e.signal_entry != null && (
                  <span className="num">signal {e.signal_entry}</span>
                )}
                {e.tp_hits != null && e.action === "entered" && <span>TP hits {e.tp_hits}</span>}
                {e.broker_confirmed && <span className="text-pos">broker confirmed</span>}
                {e.journaled_at && <span>{new Date(e.journaled_at).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>}
              </div>
              {e.notes ? (
                <p className="mt-2.5 rounded-xl border border-[rgba(var(--warm-rgb),0.08)] bg-[rgba(var(--warm-rgb),0.035)] px-3.5 py-2.5 text-[11.5px] italic leading-relaxed text-txt-mid">
                  "{e.notes}"
                </p>
              ) : (
                <p className="mt-2 text-[10.5px] text-txt-faint">no note written</p>
              )}
            </Glass>
          );
        })}
      </div>
    </div>
  );
}
