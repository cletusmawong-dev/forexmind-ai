import { useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { ConnectionState, DemoTag, Divider, Eyebrow, Glass, Pill, ChevronSection } from "../components/ui";
import { fmtR } from "../lib/format";

export function AnalyticsScreen() {
  const [open, setOpen] = useState<string | null>("distribution");
  const { data, loading } = usePolling<any>(() => api.get(endpoints.analytics), 10000);
  if (!data) return loading ? <div className="py-20" /> : <ConnectionState label="Can't load analytics" />;
  const a = data;
  const m = a?.metrics ?? {};

  const rDist = Object.entries(a?.r_distribution ?? {}).map(([k, v]) => ({ bucket: k.replace("R", ""), count: v as number }));
  const strat = Object.entries(a?.strategy_comparison ?? {}).map(([k, v]: [string, any]) => ({ name: k.split(" ")[0], win_rate: v.win_rate, total_r: v.total_r }));
  const market = Object.entries(a?.market_comparison ?? {}).map(([k, v]: [string, any]) => ({ name: k, win_rate: v.win_rate, total_r: v.total_r }));
  const curve = a?.equity_curve ?? [];

  const toggle = (k: string) => setOpen(open === k ? null : k);

  return (
    <div className="animate-fadeUp">
      <header className="mb-7 flex items-start justify-between">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight">Analytics</h1>
          <p className="mt-1 text-[12.5px] text-txt-low">What the research actually shows.</p>
        </div>
        <DemoTag />
      </header>

      {/* hero numbers - typography only */}
      <section className="flex flex-wrap items-end gap-x-10 gap-y-5 px-1">
        <HeroNum label="Total" value={fmtR(m.total_r)} tone={m.total_r >= 0 ? "text-pos" : "text-neg"} />
        <HeroNum label="Win rate" value={`${m.win_rate ?? 0}%`} />
        <HeroNum label="Profit factor" value={String(m.profit_factor ?? "-")} />
        <HeroNum label="Max drawdown" value={`${m.max_drawdown_r ?? 0}R`} tone="text-neg" />
      </section>

      {/* equity */}
      <Glass className="mt-8 !p-5">
        <div className="eyebrow mb-3 px-1">Equity curve - cumulative R</div>
        <ResponsiveContainer width="100%" height={190}>
          <AreaChart data={curve} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="aneq" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#7CD5F2" stopOpacity={0.26} />
                <stop offset="100%" stopColor="#7CD5F2" stopOpacity={0} />
              </linearGradient>
            </defs>
            <Tooltip
              cursor={{ stroke: "rgba(255,255,255,0.08)" }}
              contentStyle={{ background: "rgba(16,19,26,0.95)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 14, fontSize: 11 }}
              formatter={(v: any) => [fmtR(v), "Cumulative"]}
              labelFormatter={() => ""}
            />
            <Area type="monotone" dataKey="cum_r" stroke="#7CD5F2" strokeWidth={1.7} fill="url(#aneq)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </Glass>

      {/* TP performance - quiet rows */}
      <Glass className="mt-4 !py-2" pad={false}>
        <div className="px-5">
          {[
            ["TP1 reached", m.tp1_hit_rate, "pos"],
            ["TP2 reached", m.tp2_hit_rate, "pos"],
            ["TP3 reached", m.tp3_hit_rate, "pos"],
            ["Stopped first", m.sl_rate, "neg"],
          ].map(([label, v, tone]: any[], i: number) => (
            <div key={label as string}>
              {i > 0 && <Divider />}
              <div className="flex items-center gap-4 py-3.5">
                <span className="w-28 text-[12px] text-txt-low">{label}</span>
                <div className="h-[3px] flex-1 overflow-hidden rounded-full bg-white/[0.06]">
                  <div className={`h-full rounded-full ${tone === "neg" ? "bg-neg/70" : "bg-pos/70"}`} style={{ width: `${v ?? 0}%` }} />
                </div>
                <span className="num w-10 text-right text-[12px] font-medium text-txt-hi">{v ?? 0}%</span>
              </div>
            </div>
          ))}
        </div>
      </Glass>

      {/* expandable sections */}
      <Section open={open === "distribution"} onToggle={() => toggle("distribution")} title="R distribution">
        <ResponsiveContainer width="100%" height={150}>
          <BarChart data={rDist} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <XAxis dataKey="bucket" tick={{ fill: "#69748C", fontSize: 9 }} axisLine={false} tickLine={false} />
            <Tooltip cursor={{ fill: "rgba(255,255,255,0.03)" }} contentStyle={{ background: "rgba(16,19,26,0.95)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 14, fontSize: 11 }} />
            <Bar dataKey="count" radius={[5, 5, 2, 2]} maxBarSize={26}>
              {rDist.map((d, i) => (
                <Cell key={i} fill={d.bucket.includes("-") || d.bucket.startsWith("<") ? "#F0788C" : "#3ECF8E"} fillOpacity={0.65} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Section>

      <Section open={open === "markets"} onToggle={() => toggle("markets")} title="By strategy & market">
        <div className="space-y-5">
          <Bars title="Strategy" data={strat} />
          <Bars title="Market" data={market} />
        </div>
      </Section>

      <Section open={open === "experiments"} onToggle={() => toggle("experiments")} title="Learning experiments">
        {(a?.experiments ?? []).length === 0 ? (
          <p className="text-[12px] text-txt-faint">No experiments yet.</p>
        ) : (
          <div className="divide-y divide-white/[0.05]">
            {(a?.experiments ?? []).map((e: any, i: number) => (
              <div key={i} className="flex items-center justify-between py-3">
                <span className="text-[12px] text-txt-mid">
                  <span className="font-mono text-txt-low">{e.variable}</span> {String(e.old)} → {String(e.new)}
                </span>
                <span className="flex items-center gap-3">
                  <span className="num text-[11px] text-txt-low">
                    {e.orig_wr}% → <span className="font-medium text-txt-hi">{e.exp_wr}%</span>
                  </span>
                  <Pill tone={e.result === "IMPROVED" ? "pos" : e.result === "WORSE" ? "neg" : "neutral"}>{e.result.replace(/_/g, " ").toLowerCase()}</Pill>
                </span>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Link to="/learning" className="btn-ghost mt-6 w-full">Open the Learning Lab</Link>
    </div>
  );
}

function HeroNum({ label, value, tone = "text-txt-hi" }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div className={`num mt-1.5 text-[30px] font-light leading-none ${tone}`}>{value}</div>
    </div>
  );
}

function Bars({ title, data }: { title: string; data: { name: string; win_rate: number; total_r: number }[] }) {
  return (
    <div>
      <div className="eyebrow !text-[9.5px] mb-3">{title}</div>
      <ResponsiveContainer width="100%" height={110}>
        <BarChart data={data} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
          <XAxis dataKey="name" tick={{ fill: "#69748C", fontSize: 9.5 }} axisLine={false} tickLine={false} />
          <Tooltip cursor={{ fill: "rgba(255,255,255,0.03)" }} contentStyle={{ background: "rgba(16,19,26,0.95)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 14, fontSize: 11 }} />
          <Bar dataKey="win_rate" radius={[5, 5, 2, 2]} maxBarSize={30} fill="#6C9EFF" fillOpacity={0.55} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function Section({ open, onToggle, title, children }: { open: boolean; onToggle: () => void; title: string; children: React.ReactNode }) {
  return (
    <Glass className="mt-4 !p-0" pad={false}>
      <button onClick={onToggle} className="tap flex w-full items-center justify-between px-5 py-4">
        <span className="text-[13px] font-medium text-txt-hi">{title}</span>
        <ChevronSection open={open} />
      </button>
      <div className={`grid transition-all duration-500 ease-out ${open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
        <div className="overflow-hidden">
          <div className="px-5 pb-5">{children}</div>
        </div>
      </div>
    </Glass>
  );
}
