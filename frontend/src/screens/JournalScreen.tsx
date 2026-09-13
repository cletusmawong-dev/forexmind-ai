import { useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, ResponsiveContainer, Tooltip } from "recharts";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Signal } from "../lib/types";
import { ConnectionState, DemoTag, Divider, Eyebrow, Glass, Pill, Segmented, Spinner } from "../components/ui";
import { fmtDateTime, fmtR } from "../lib/format";
import { useNavigate } from "react-router-dom";

export function JournalScreen() {
  const [tab, setTab] = useState<"record" | "history">("record");
  const stats = usePolling<any>(() => api.get(endpoints.journal), 8000);
  const signals = usePolling<{ signals: Signal[] }>(() => api.get(`${endpoints.signals}?status=closed&limit=30`), 8000);
  const navigate = useNavigate();

  if (!stats.data) {
    if (stats.loading) return <Spinner label="Opening the journal…" />;
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
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight">Journal</h1>
          <p className="mt-1 text-[12.5px] text-txt-low">The complete research record.</p>
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
                <span className="num font-bold text-txt-hi">{j?.total_signals ?? 0}</span> signals ·{" "}
                <span className="num font-bold text-txt-hi">{m.win_rate ?? 0}%</span> win rate ·{" "}
                <span className="num font-bold text-txt-hi">{fmtR(m.avg_r)}</span> avg ·{" "}
                <span className="num font-bold text-txt-hi">{m.profit_factor ?? "–"}</span> profit factor
              </p>
            </section>

            {/* equity curve */}
            <div className="mt-8">
              <EquityChart curve={(j?.metrics && j?.daily && buildCurve(daily)) ?? []} />
            </div>

            {/* strategy performance — quiet rows */}
            <Eyebrow className="mb-1 mt-9">By strategy</Eyebrow>
            <Glass pad={false} className="mt-2 divide-y divide-white/[0.05] !p-0">
              {Object.entries(j?.by_strategy ?? {}).map(([name, v]: [string, any]) => (
                <div key={name} className="flex items-center justify-between px-5 py-4">
                  <span className="text-[12.5px] text-txt-mid">{name}</span>
                  <span className="num text-[12px] text-txt-low">
                    {v.signals} signals · <span className="font-bold text-txt-hi">{v.win_rate}%</span> · <span className="font-bold text-txt-hi">{fmtR(v.avg_r)}</span>
                  </span>
                </div>
              ))}
            </Glass>
          </div>

          <div className="mt-10 lg:col-span-5 lg:mt-0 lg:border-l lg:border-white/[0.05] lg:pl-10">
            <Eyebrow className="mb-3">Daily R · last 14 days</Eyebrow>
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
          <Eyebrow className="mb-2">Recent completed signals</Eyebrow>
          <Glass pad={false} className="divide-y divide-white/[0.05] !p-0">
            {(signals.data?.signals ?? []).map((s) => (
              <button key={s.id} onClick={() => navigate(`/signals/${s.id}`)} className="tap flex w-full items-center gap-4 px-5 py-4 text-left transition hover:bg-white/[0.02]">
                <div className="w-16 shrink-0">
                  <div className="text-[12.5px] font-semibold">{s.market}</div>
                  <div className={`text-[9px] font-bold tracking-wider ${s.direction === "BUY" ? "text-pos" : "text-neg"}`}>{s.direction}</div>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[11px] text-txt-low">{s.strategy_name}</div>
                  <div className="text-[9px] text-txt-faint">{fmtDateTime(s.candle_time)} · {(s.market_conditions as any)?.session}</div>
                </div>
                {s.user_action && <Pill tone={s.user_action === "entered" ? "cyan" : "neutral"}>{s.user_action === "entered" ? "taken" : "skipped"}</Pill>}
                <span className={`num w-12 shrink-0 text-right text-[13px] font-semibold ${s.r_multiple > 0 ? "text-pos" : s.r_multiple < 0 ? "text-neg" : "text-txt-low"}`}>
                  {fmtR(s.r_multiple)}
                </span>
              </button>
            ))}
          </Glass>
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
